// src/config.ts — ENFORCES PRODUCTION SAFETY
import * as fs from 'fs';

const REQUIRED_PRODUCTION = [
  'PRIVATE_KEY',
  'SESSION_SECRET',
  'ALCHEMY_ETH_KEY',
  'DATABASE_PASSWORD',
];

const MUST_CHANGE = [
  'dev-change-in-production',
  'dev-only-secret',
];

export function validateConfig() {
  const env = process.env.NODE_ENV || 'development';
  const isDev = env === 'development';

  // Production enforcement
  if (!isDev) {
    for (const key of REQUIRED_PRODUCTION) {
      if (!process.env[key]) {
        throw new Error(`FATAL: ${key} not set in production. Use secrets manager.`);
      }
      if (MUST_CHANGE.some(bad => process.env[key]?.includes(bad))) {
        throw new Error(
          `FATAL: ${key} contains dev placeholder. Set via secrets manager.`
        );
      }
    }
  }

  // Development warning
  if (isDev && process.env.SESSION_SECRET?.includes('dev-only')) {
    console.warn('⚠️  SESSION_SECRET uses dev placeholder — change before production');
  }
}

export const CONFIG = {
  privateKey: process.env.PRIVATE_KEY || '',
  sessionSecret: process.env.SESSION_SECRET || 'dev-only-secret-not-for-production',
  isDryRun: process.env.DRY_RUN === 'true',
  minProfitUSD: parseFloat(process.env.MIN_PROFIT_USD || '2.0'),
  maxLoanUSD: parseFloat(process.env.MAX_LOAN_USD || '500000'),
};
