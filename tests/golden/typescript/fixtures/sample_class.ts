import { EventEmitter } from "events";
import type { Config } from "./config";

/**
 * Orchestrates a long-running battle between two players.
 * Exercises: class split into methods (span > 50 lines), fields, JSDoc.
 */
export class BattleEngine extends EventEmitter {
  public readonly id: string;
  private rounds: number = 0;
  protected attacker: string;

  constructor(id: string, attacker: string) {
    super();
    this.id = id;
    this.attacker = attacker;
  }

  /** Runs a single round and returns the surviving troop count. */
  public runRound(troopsA: number, troopsB: number): number {
    this.rounds += 1;
    const malus = this.computeMalus(troopsA, troopsB);
    let survivors = troopsA - troopsB - malus;
    if (survivors < 0) {
      survivors = 0;
      this.emit("defeat", this.attacker);
    }
    for (let i = 0; i < this.rounds; i++) {
      survivors = Math.max(0, survivors - 1);
    }
    return survivors;
  }

  private computeMalus(a: number, b: number): number {
    const ratio = a === 0 ? 0 : b / a;
    if (ratio > 2) {
      return 80;
    } else if (ratio > 1) {
      return 40;
    }
    return 10;
  }

  public async resolve(troopsA: number, troopsB: number): Promise<string> {
    let a = troopsA;
    let b = troopsB;
    while (a > 0 && b > 0) {
      a = this.runRound(a, b);
      b = this.runRound(b, a);
    }
    return a > b ? this.attacker : "defender";
  }

  public summary(): string {
    const parts = [
      `engine=${this.id}`,
      `attacker=${this.attacker}`,
      `rounds=${this.rounds}`,
    ];
    return parts.join(" | ");
  }

  public reset(): void {
    this.rounds = 0;
  }
}
