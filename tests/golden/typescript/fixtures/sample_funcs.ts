import { clamp } from "./math";

/** A small configuration constant block (exercises merge of short nodes). */
const DEFAULT_MALUS = 10;
const MAX_TROOPS = 1000;
const MIN_TROOPS = 0;

export interface TroopState {
  count: number;
  morale: number;
}

/**
 * Combines two troop pools with a flat malus. Deliberately long (> 10 lines
 * span) so it is emitted as its own standalone function chunk.
 */
export function combineTroops(a: TroopState, b: TroopState): TroopState {
  const rawCount = a.count + b.count - DEFAULT_MALUS;
  const count = clamp(rawCount, MIN_TROOPS, MAX_TROOPS);
  let morale = Math.round((a.morale + b.morale) / 2);
  if (count <= MIN_TROOPS) {
    morale = 0;
  }
  if (morale > 100) {
    morale = 100;
  }
  return { count, morale };
}

export function isRouted(state: TroopState): boolean {
  return state.count <= MIN_TROOPS || state.morale < 5;
}

function internalHelper(n: number): number {
  return n * 2;
}
