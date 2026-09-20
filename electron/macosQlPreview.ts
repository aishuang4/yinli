/**
 * 加载桌面端内嵌系统预览：macOS QLPreviewView，Windows IPreviewHandler。
 */

import { existsSync } from 'fs';
import * as path from 'path';

type NativePreview = {
  show: (...args: unknown[]) => number;
  update: (x: number, y: number, width: number, height: number) => number;
  close: () => void;
};

let native: NativePreview | null | undefined;

function nativeModulePath(): string {
  const unpacked = path.join(__dirname, 'native', 'system_preview.node');
  const asarUnpacked = unpacked.replace(`${path.sep}app.asar${path.sep}`, `${path.sep}app.asar.unpacked${path.sep}`);
  if (existsSync(unpacked)) return unpacked;
  return asarUnpacked;
}

function loadNative(): NativePreview | null {
  if (process.platform !== 'darwin' && process.platform !== 'win32') {
    return null;
  }
  if (native !== undefined) {
    return native;
  }
  const modulePath = nativeModulePath();
  if (!existsSync(modulePath)) {
    native = null;
    return native;
  }
  try {
    native = require(modulePath) as NativePreview;
  } catch (error) {
    console.warn('[Electron] Failed to load embedded system preview', error);
    native = null;
  }
  return native;
}

export function canEmbedSystemPreview(): boolean {
  return loadNative() != null;
}

export function showEmbeddedPreview(
  filePath: string,
  x: number,
  y: number,
  width: number,
  height: number,
  hwnd?: Buffer,
): boolean {
  const addon = loadNative();
  if (!addon) {
    return false;
  }
  if (process.platform === 'win32') {
    if (!hwnd) {
      return false;
    }
    return addon.show(hwnd, filePath, x, y, width, height) === 0;
  }
  return addon.show(filePath, x, y, width, height) === 0;
}

export function updateEmbeddedPreview(
  x: number,
  y: number,
  width: number,
  height: number,
): void {
  loadNative()?.update(x, y, width, height);
}

export function closeEmbeddedPreview(): void {
  loadNative()?.close();
}
