/*
 * Copyright (c) KMG. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

export const COMPARISON_DESCRIPTOR_SCHEMA_VERSION = 2;
export const DESCRIPTOR_LOAD_ATTEMPTS = 11;
export const DESCRIPTOR_RETRY_DELAY_MS = 500;
export const DESCRIPTOR_MAX_RETRY_DELAY_MS = 5000;
const HTTP_NOT_FOUND = 404;

export class DescriptorNotReadyError extends Error {
  constructor(message = 'The comparison descriptor has not been refreshed yet') {
    super(message);
    this.name = 'DescriptorNotReadyError';
  }
}

export class DescriptorLoadCancelledError extends Error {
  constructor() {
    super('Comparison descriptor loading was cancelled');
    this.name = 'DescriptorLoadCancelledError';
  }
}

interface LoadOptions {
  signal?: AbortSignal;
  attempts?: number;
  retryDelayMilliseconds?: number;
  wait?: (milliseconds: number, signal?: AbortSignal) => Promise<void>;
}

export function errorStatus(error: unknown): number | undefined {
  if (!error || typeof error !== 'object') return undefined;
  const direct = (error as {status?: unknown}).status;
  if (typeof direct === 'number') return direct;
  const nested = (error as {response?: {status?: unknown}}).response?.status;
  return typeof nested === 'number' ? nested : undefined;
}

export async function loadComparisonDescriptor<T>(
  request: () => Promise<T>,
  options: LoadOptions = {}
): Promise<T> {
  const attempts = options.attempts || DESCRIPTOR_LOAD_ATTEMPTS;
  const retryDelay = options.retryDelayMilliseconds ?? DESCRIPTOR_RETRY_DELAY_MS;
  const wait = options.wait || abortableDelay;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    if (options.signal?.aborted) throw new DescriptorLoadCancelledError();
    try {
      const result = await request();
      if (options.signal?.aborted) throw new DescriptorLoadCancelledError();
      return result;
    } catch (error) {
      if (options.signal?.aborted) throw new DescriptorLoadCancelledError();
      const retryable = error instanceof DescriptorNotReadyError || errorStatus(error) === HTTP_NOT_FOUND;
      if (!retryable || attempt === attempts) throw error;
      const boundedDelay = Math.min(retryDelay * (2 ** (attempt - 1)), DESCRIPTOR_MAX_RETRY_DELAY_MS);
      await wait(boundedDelay, options.signal);
    }
  }
  throw new DescriptorNotReadyError();
}

export function descriptorRetryWindowMilliseconds(
  attempts = DESCRIPTOR_LOAD_ATTEMPTS,
  initialDelay = DESCRIPTOR_RETRY_DELAY_MS
): number {
  let total = 0;
  for (let attempt = 1; attempt < attempts; attempt += 1) {
    total += Math.min(initialDelay * (2 ** (attempt - 1)), DESCRIPTOR_MAX_RETRY_DELAY_MS);
  }
  return total;
}

function abortableDelay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(new DescriptorLoadCancelledError());
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DescriptorLoadCancelledError());
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, milliseconds);
    signal?.addEventListener('abort', onAbort, {once: true});
  });
}
