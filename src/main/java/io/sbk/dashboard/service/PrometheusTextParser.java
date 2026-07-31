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

package io.sbk.dashboard.service;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Parser for the Prometheus text exposition format emitted by SBK and SBM Micrometer registries. */
public final class PrometheusTextParser {
    /**
     * Parses all valid samples in a response, ignoring HELP/TYPE comments and malformed lines.
     *
     * @param text Prometheus text response
     * @return parsed numeric samples
     */
    public List<ParsedMetric> parse(String text) {
        List<ParsedMetric> metrics = new ArrayList<>();
        for (String line : text.split("\\R")) {
            ParsedMetric metric = parseLine(line.trim());
            if (metric != null) {
                metrics.add(metric);
            }
        }
        return metrics;
    }

    private ParsedMetric parseLine(String line) {
        if (line.isEmpty() || line.charAt(0) == '#') {
            return null;
        }
        int separator = sampleSeparator(line);
        if (separator < 1) {
            return null;
        }
        String identity = line.substring(0, separator);
        String remainder = line.substring(separator).trim();
        int valueEnd = whitespace(remainder);
        String valueText = valueEnd < 0 ? remainder : remainder.substring(0, valueEnd);
        try {
            double value = parseValue(valueText);
            int labelsStart = identity.indexOf('{');
            if (labelsStart < 0) {
                return new ParsedMetric(identity, Map.of(), value);
            }
            if (!identity.endsWith("}") || labelsStart == 0) {
                return null;
            }
            String name = identity.substring(0, labelsStart);
            Map<String, String> labels = parseLabels(identity.substring(labelsStart + 1, identity.length() - 1));
            return new ParsedMetric(name, labels, value);
        } catch (IllegalArgumentException exception) {
            return null;
        }
    }

    private int sampleSeparator(String line) {
        boolean quoted = false;
        boolean escaped = false;
        for (int index = 0; index < line.length(); index++) {
            char character = line.charAt(index);
            if (escaped) {
                escaped = false;
            } else if (character == '\\') {
                escaped = true;
            } else if (character == '"') {
                quoted = !quoted;
            } else if (!quoted && Character.isWhitespace(character)) {
                return index;
            }
        }
        return -1;
    }

    private int whitespace(String text) {
        for (int index = 0; index < text.length(); index++) {
            if (Character.isWhitespace(text.charAt(index))) {
                return index;
            }
        }
        return -1;
    }

    private double parseValue(String value) {
        return switch (value) {
            case "+Inf" -> Double.POSITIVE_INFINITY;
            case "-Inf" -> Double.NEGATIVE_INFINITY;
            case "NaN" -> Double.NaN;
            default -> Double.parseDouble(value);
        };
    }

    private Map<String, String> parseLabels(String text) {
        if (text.isBlank()) {
            return Map.of();
        }
        LinkedHashMap<String, String> labels = new LinkedHashMap<>();
        int position = 0;
        while (position < text.length()) {
            int equals = text.indexOf('=', position);
            if (equals <= position) {
                throw new IllegalArgumentException("Invalid label");
            }
            String name = text.substring(position, equals).trim();
            position = equals + 1;
            if (position >= text.length() || text.charAt(position) != '"') {
                throw new IllegalArgumentException("Invalid label value");
            }
            StringBuilder value = new StringBuilder();
            position++;
            boolean closed = false;
            while (position < text.length()) {
                char character = text.charAt(position++);
                if (character == '"') {
                    closed = true;
                    break;
                }
                if (character == '\\' && position < text.length()) {
                    char escaped = text.charAt(position++);
                    value.append(escaped == 'n' ? '\n' : escaped);
                } else {
                    value.append(character);
                }
            }
            if (!closed) {
                throw new IllegalArgumentException("Unclosed label value");
            }
            labels.put(name, value.toString());
            if (position < text.length()) {
                if (text.charAt(position) != ',') {
                    throw new IllegalArgumentException("Invalid label separator");
                }
                position++;
                while (position < text.length() && Character.isWhitespace(text.charAt(position))) {
                    position++;
                }
            }
        }
        return Collections.unmodifiableMap(labels);
    }

    /** Parsed metric identity and value. */
    public record ParsedMetric(String name, Map<String, String> labels, double value) { }
}
