/*
 * Copyright (c) KMG. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

module.exports = {
  cacheDirectory: '<rootDir>/.jest-cache',
  clearMocks: true,
  extensionsToTreatAsEsm: ['.ts', '.tsx'],
  moduleFileExtensions: ['ts', 'tsx', 'js'],
  testEnvironment: 'jsdom',
  testMatch: ['<rootDir>/tests/**/*.test.ts'],
  transform: {'^.+\\.(t|j)sx?$': ['@swc/jest']},
};
