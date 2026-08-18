/*
 * Copyright (c) KMG. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

import {
  DescriptorLoadCancelledError,
  DescriptorNotReadyError,
  errorStatus,
  loadComparisonDescriptor,
} from '../src/descriptor';

describe('comparison descriptor loading', () => {
  const immediateWait = jest.fn(async () => undefined);

  beforeEach(() => immediateWait.mockClear());

  it('retries a missing descriptor until Grafana provisions it', async () => {
    const request = jest.fn()
      .mockRejectedValueOnce({status: 404})
      .mockRejectedValueOnce({status: 404})
      .mockResolvedValue('ready');
    await expect(loadComparisonDescriptor(request, {wait: immediateWait})).resolves.toBe('ready');
    expect(request).toHaveBeenCalledTimes(3);
    expect(immediateWait).toHaveBeenCalledTimes(2);
  });

  it('retries a stale descriptor that lacks current metadata', async () => {
    const request = jest.fn()
      .mockRejectedValueOnce(new DescriptorNotReadyError())
      .mockResolvedValue('refreshed');
    await expect(loadComparisonDescriptor(request, {wait: immediateWait})).resolves.toBe('refreshed');
    expect(request).toHaveBeenCalledTimes(2);
  });

  it('stops after the bounded number of attempts', async () => {
    const request = jest.fn().mockRejectedValue({status: 404});
    await expect(loadComparisonDescriptor(request, {attempts: 3, wait: immediateWait})).rejects.toEqual({status: 404});
    expect(request).toHaveBeenCalledTimes(3);
    expect(immediateWait).toHaveBeenCalledTimes(2);
  });

  it('does not retry other backend errors', async () => {
    const request = jest.fn().mockRejectedValue({response: {status: 503}});
    await expect(loadComparisonDescriptor(request, {wait: immediateWait})).rejects.toEqual({response: {status: 503}});
    expect(request).toHaveBeenCalledTimes(1);
    expect(errorStatus({response: {status: 503}})).toBe(503);
  });

  it('cancels before making another request', async () => {
    const controller = new AbortController();
    controller.abort();
    const request = jest.fn();
    await expect(loadComparisonDescriptor(request, {signal: controller.signal})).rejects
      .toBeInstanceOf(DescriptorLoadCancelledError);
    expect(request).not.toHaveBeenCalled();
  });
});
