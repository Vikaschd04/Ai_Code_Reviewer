package com.acme.core.util;

import com.google.common.base.Preconditions;

public final class Strings {
    private Strings() {
    }

    public static boolean isBlank(String value) {
        Preconditions.checkNotNull(value);
        return value.trim().isEmpty();
    }
}
