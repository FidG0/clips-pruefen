package de.clipwreck.app;

import android.content.Context;

final class BuildConfigInfo {
    private BuildConfigInfo() {}

    static String versionName(Context c) {
        try {
            return c.getPackageManager().getPackageInfo(c.getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "?";
        }
    }
}
