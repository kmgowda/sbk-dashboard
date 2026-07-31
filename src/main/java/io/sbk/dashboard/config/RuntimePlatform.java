/**
 * Copyright (c) KMG. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package io.sbk.dashboard.config;

import java.util.Locale;

/** A normalized native operating-system and processor combination. */
public record RuntimePlatform(OperatingSystem operatingSystem, Architecture architecture) {
    /** Returns the platform for the running JVM. */
    public static RuntimePlatform current() {
        return from(System.getProperty("os.name"), System.getProperty("os.arch"));
    }

    /** Normalizes JVM operating-system and architecture names. */
    public static RuntimePlatform from(String osName, String osArchitecture) {
        String os = osName.toLowerCase(Locale.ROOT);
        OperatingSystem operatingSystem;
        if (os.contains("mac") || os.contains("darwin")) {
            operatingSystem = OperatingSystem.MACOS;
        } else if (os.contains("win")) {
            operatingSystem = OperatingSystem.WINDOWS;
        } else if (os.contains("linux")) {
            operatingSystem = OperatingSystem.LINUX;
        } else {
            throw new IllegalArgumentException("Unsupported operating system: " + osName);
        }
        String arch = osArchitecture.toLowerCase(Locale.ROOT).replace('-', '_');
        Architecture architecture = switch (arch) {
            case "amd64", "x86_64", "x64" -> Architecture.X86_64;
            case "aarch64", "arm64" -> Architecture.ARM64;
            default -> throw new IllegalArgumentException("Unsupported architecture: " + osArchitecture);
        };
        return new RuntimePlatform(operatingSystem, architecture);
    }

    /** Returns the stable key used by native download properties. */
    public String id() {
        return operatingSystem.id + '-' + architecture.id;
    }

    /** Returns whether native executable names require the .exe suffix. */
    public boolean windows() {
        return operatingSystem == OperatingSystem.WINDOWS;
    }

    /** Supported operating systems. */
    public enum OperatingSystem {
        LINUX("linux"), MACOS("macos"), WINDOWS("windows");

        private final String id;

        OperatingSystem(String id) {
            this.id = id;
        }
    }

    /** Supported processor architectures. */
    public enum Architecture {
        X86_64("x86_64"), ARM64("arm64");

        private final String id;

        Architecture(String id) {
            this.id = id;
        }
    }
}
