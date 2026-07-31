/**
 * Copyright (c) KMG. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0.
 */

package io.sbk.dashboard.config;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

/** Tests stable native platform normalization without depending on the test host. */
class RuntimePlatformTest {
    /** JVM aliases resolve to the platform keys used by the native archive manifest. */
    @ParameterizedTest
    @CsvSource({
        "Linux, amd64, linux-x86_64",
        "Linux, aarch64, linux-arm64",
        "Mac OS X, x86_64, macos-x86_64",
        "Darwin, arm64, macos-arm64",
        "Windows 11, amd64, windows-x86_64",
        "Windows Server 2025, aarch64, windows-arm64"
    })
    void normalizesSupportedPlatforms(String os, String architecture, String expected) {
        assertEquals(expected, RuntimePlatform.from(os, architecture).id());
    }

    /** Unsupported operating systems and architectures fail with actionable validation. */
    @ParameterizedTest
    @CsvSource({"FreeBSD, amd64", "Linux, riscv64"})
    void rejectsUnsupportedPlatforms(String os, String architecture) {
        assertThrows(IllegalArgumentException.class, () -> RuntimePlatform.from(os, architecture));
    }
}
