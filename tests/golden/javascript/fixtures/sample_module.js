import { readFile } from "fs/promises";

/** Short constant block (merge candidates). */
const RETRY_LIMIT = 3;
const TIMEOUT_MS = 5000;

/**
 * Simple in-memory cache with TTL, used to exercise JS class chunking.
 */
export class Cache {
  constructor(ttlMs) {
    this.ttlMs = ttlMs;
    this.store = new Map();
  }

  set(key, value) {
    this.store.set(key, { value, at: Date.now() });
  }

  get(key) {
    const entry = this.store.get(key);
    if (!entry) {
      return undefined;
    }
    if (Date.now() - entry.at > this.ttlMs) {
      this.store.delete(key);
      return undefined;
    }
    return entry.value;
  }

  clear() {
    this.store.clear();
  }
}

export async function loadConfig(path) {
  let attempts = 0;
  while (attempts < RETRY_LIMIT) {
    try {
      const raw = await readFile(path, "utf-8");
      return JSON.parse(raw);
    } catch (err) {
      attempts += 1;
    }
  }
  return null;
}

function unused(x) {
  return x;
}
