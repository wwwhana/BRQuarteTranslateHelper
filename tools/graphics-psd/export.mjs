#!/usr/bin/env node

import { existsSync, mkdirSync, readFileSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { createCanvas, ImageData } from '@napi-rs/canvas';
import { initializeCanvas, readPsd, writePsdBuffer } from 'ag-psd';

initializeCanvas(() => { throw new Error('Canvas is not used by the PSD reader'); },
  (width, height) => ({ width, height, data: new Uint8ClampedArray(width * height * 4) }));

const LAYER_NAMES = ['Translation', 'Retouch', 'Original'];
const die = (message) => { throw new Error(message); };
const requiredFile = (value, label) => {
  if (!value) die(`${label} is required`);
  const resolved = path.resolve(value);
  try { if (!statSync(resolved).isFile()) die(`${label} is not a file: ${resolved}`); }
  catch { die(`${label} does not exist: ${resolved}`); }
  return resolved;
};

// Pillow only decodes PNGs; it does not paint or composite them.  This keeps
// RGB bytes under alpha=0 intact for the locked original layer and restore.
const PNG_DECODER = `from PIL import Image\nimport struct,sys\nim=Image.open(sys.argv[1]); im.load()\nif sys.argv[2]=="mask":\n im=im.convert("L"); b=bytearray()\n for v in im.tobytes(): b.extend((v,v,v,255))\nelse:\n im=im.convert("RGBA"); b=im.tobytes()\nsys.stdout.buffer.write(struct.pack(">II",im.width,im.height)+b)`;
function png(file, mask = false, python = process.env.BRQ_PYTHON || 'python3') {
  const result = spawnSync(python, ['-c', PNG_DECODER, file, mask ? 'mask' : 'rgba'], {
    maxBuffer: 512 * 1024 * 1024,
  });
  if (result.status !== 0) die(`PNG decode failed for ${file}: ${(result.stderr || '').toString().trim()}`);
  if (result.stdout.length < 8) die(`PNG decode returned no image: ${file}`);
  const width = result.stdout.readUInt32BE(0), height = result.stdout.readUInt32BE(4);
  const data = new Uint8ClampedArray(result.stdout.subarray(8));
  if (data.length !== width * height * 4) die(`invalid RGBA data in ${file}`);
  return { width, height, data };
}

const image = (data) => new ImageData(new Uint8ClampedArray(data.data), data.width, data.height);
const canvasFor = (data) => {
  const canvas = createCanvas(data.width, data.height);
  canvas.getContext('2d').putImageData(image(data), 0, 0);
  return canvas;
};
const maskCanvas = (data) => {
  const pixels = new Uint8ClampedArray(data.data);
  for (let i = 0; i < pixels.length; i += 4) {
    const value = pixels[i];
    pixels[i] = pixels[i + 1] = pixels[i + 2] = 255;
    pixels[i + 3] = value;
  }
  return canvasFor({ width: data.width, height: data.height, data: pixels });
};
const equal = (a, b) => a && b && a.width === b.width && a.height === b.height &&
  a.data.length === b.data.length && a.data.every((value, index) => value === b.data[index]);
const diff = (a, b) => {
  if (!a || !b || a.width !== b.width || a.height !== b.height) return { ok: false, mismatches: -1, max: 255 };
  let mismatches = 0, max = 0;
  for (let i = 0; i < a.data.length; i += 4) {
    const alpha = Math.abs(a.data[i + 3] - b.data[i + 3]);
    if (alpha > 1) mismatches++;
    if (alpha > max) max = alpha;
    for (let channel = 0; channel < 3; channel++) {
      const delta = Math.abs(Math.round(a.data[i + channel] * a.data[i + 3] / 255) -
        Math.round(b.data[i + channel] * b.data[i + 3] / 255));
      if (delta > 1) mismatches++;
      if (delta > max) max = delta;
    }
  }
  return { ok: mismatches === 0, mismatches, max };
};

function composite(original, restoration, mask) {
  const canvas = canvasFor(original), context = canvas.getContext('2d');
  if (mask) {
    context.globalCompositeOperation = 'destination-in';
    context.drawImage(maskCanvas(mask), 0, 0);
    context.globalCompositeOperation = 'source-over';
  }
  context.drawImage(canvasFor(restoration), 0, 0);
  return context.getImageData(0, 0, original.width, original.height);
}

function expectedLayers(original, restoration, mask) {
  const blank = { width: original.width, height: original.height,
    data: new Uint8ClampedArray(original.width * original.height * 4) };
  return [
    { name: LAYER_NAMES[0], imageData: blank },
    { name: LAYER_NAMES[1], imageData: restoration },
    {
      name: LAYER_NAMES[2], imageData: original,
      protected: { position: true, composite: true, transparency: true },
      transparencyProtected: true,
      ...(mask ? { mask: { imageData: mask, defaultColor: 255, fromVectorData: false } } : {}),
    },
  ];
}

function readback(buffer, expected) {
  const psd = readPsd(buffer, { useImageData: true, useRawThumbnail: true, skipThumbnail: true });
  const layers = psd.children || [], checks = [];
  const check = (name, ok, detail) => checks.push({ name, ok: !!ok, ...(detail ? { detail } : {}) });
  check('document dimensions', psd.width === expected.original.width && psd.height === expected.original.height);
  check('three overlay layers', layers.length === 3 && layers.map((layer) => layer.name).join('\0') === LAYER_NAMES.join('\0'),
    layers.map((layer) => layer.name || ''));
  check('translation layer is blank', equal(layers[0]?.imageData, expected.layers[0].imageData) && !layers[0]?.text);
  check('restoration round-trip exactly', equal(layers[1]?.imageData, expected.restoration));
  check('original RGBA round-trip exactly', equal(layers[2]?.imageData, expected.original));
  check('original layer is locked', layers[2]?.protected?.position === true && layers[2]?.protected?.composite === true &&
    layers[2]?.protected?.transparency === true && layers[2]?.transparencyProtected === true);
  if (expected.mask) check('original mask round-trip exactly', equal(layers[2]?.mask?.imageData, expected.mask));
  else check('no unexpected original mask', !layers[2]?.mask);
  const composite = diff(psd.imageData, expected.clean);
  check('PSD composite equals clean PNG', composite.ok, composite);
  return { status: checks.every((item) => item.ok) ? 'ok' : 'failed', checks };
}

function exportPsd({ original: originalPath, restoration: restorationPath, mask: maskPath, clean: cleanPath, output: outputPath,
  python: pythonPath = process.env.BRQ_PYTHON || 'python3' }) {
  const original = png(requiredFile(originalPath, 'original PNG'), false, pythonPath);
  const restoration = png(requiredFile(restorationPath, 'restoration PNG'), false, pythonPath);
  const mask = maskPath ? png(requiredFile(maskPath, 'original mask PNG'), true, pythonPath) : undefined;
  const clean = png(requiredFile(cleanPath, 'clean PNG'), false, pythonPath);
  if (![restoration, mask, clean].filter(Boolean).every((data) =>
    data.width === original.width && data.height === original.height)) {
    die('restoration, mask, and clean PNG dimensions must match original');
  }
  const expectedClean = composite(original, restoration, mask);
  const cleanDiff = diff(expectedClean, clean);
  if (!cleanDiff.ok) die(`clean PNG does not match original/mask/restoration composite: ${JSON.stringify(cleanDiff)}`);
  if (!outputPath) die('output PSD is required');
  const output = path.resolve(outputPath);
  if (existsSync(output)) die(`output already exists: ${output}`);
  const children = expectedLayers(original, restoration, mask);
  const psd = {
    width: original.width, height: original.height, channels: 3, bitsPerChannel: 8, colorMode: 3,
    imageData: clean, children,
  };
  const buffer = writePsdBuffer(psd, { compress: true, noBackground: true });
  const beforeWrite = readback(buffer, { original, restoration, mask, clean, layers: children });
  if (beforeWrite.status !== 'ok') die(`PSD read-back failed before write: ${JSON.stringify(beforeWrite)}`);
  mkdirSync(path.dirname(output), { recursive: true });
  try {
    writeFileSync(output, buffer, { flag: 'wx' });
  } catch (error) {
    die(`could not create output without overwriting: ${error.message}`);
  }
  let result;
  try {
    result = readback(readFileSync(output), { original, restoration, mask, clean, layers: children });
    if (result.status !== 'ok') die(`PSD read-back failed: ${JSON.stringify(result)}`);
  } catch (error) {
    try { unlinkSync(output); } catch { /* preserve the original error */ }
    throw error;
  }
  return { ...result, paths: { output, original: path.resolve(originalPath), restoration: path.resolve(restorationPath),
    originalMask: maskPath ? path.resolve(maskPath) : undefined, clean: path.resolve(cleanPath) } };
}

function parse(argv) {
  const args = {};
  const keys = new Map([['original', 'original'], ['restore', 'restoration'], ['mask', 'mask'], ['clean', 'clean'], ['output', 'output'], ['python', 'python']]);
  for (let index = 0; index < argv.length; index++) {
    const key = argv[index]?.replace(/^--/, '');
    if (!keys.has(key) || argv[index + 1] === undefined) die(`invalid option: ${argv[index]}`);
    args[keys.get(key)] = argv[++index];
  }
  return args;
}

const help = 'node export.mjs --original <PNG> --restore <full-canvas PNG> [--mask <grayscale PNG>] --clean <PNG> --output <PSD> [--python <executable>]';
if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv.includes('--help')) console.log(help);
    else console.log(JSON.stringify(exportPsd(parse(process.argv.slice(2))), null, 2));
  } catch (error) {
    console.error(`error: ${error.message}`);
    process.exitCode = 1;
  }
}

export { composite, exportPsd, readback };
