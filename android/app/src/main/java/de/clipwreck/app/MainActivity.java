package de.clipwreck.app;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

/**
 * Clipwreck Handy-App: eine schlanke Hülle um https://fidg0.github.io/clips-pruefen/.
 * Der Inhalt kommt immer frisch von der Seite, also bekommt die App jede Änderung an der Seite
 * automatisch, ohne neue APK. Eine neue APK braucht es nur, wenn sich diese Hülle ändert.
 */
public class MainActivity extends Activity {

    static final String START = "https://fidg0.github.io/clips-pruefen/";
    private static final String HOST = "fidg0.github.io";
    private static final String PFAD = "/clips-pruefen";

    private FrameLayout wurzel;
    private WebView web;
    private View vollbildAnsicht;
    private WebChromeClient.CustomViewCallback vollbildCallback;
    private final Chrome chrome = new Chrome();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        wurzel = new FrameLayout(this);
        wurzel.setBackgroundColor(Color.parseColor("#0C0B0E"));
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0C0B0E"));
        wurzel.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(wurzel);
        abstaendeFuerSystemleisten();

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        // Die Seite kann so erkennen, dass sie in der App läuft.
        s.setUserAgentString(s.getUserAgentString() + " ClipwreckApp/" + BuildConfigInfo.versionName(this));
        CookieManager.getInstance().setAcceptCookie(true);

        web.addJavascriptInterface(new Bruecke(), "ClipwreckApp");
        web.setWebViewClient(new Client());
        web.setWebChromeClient(chrome);
        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) ->
                herunterladen(url, userAgent, contentDisposition, mimeType));

        if (savedInstanceState != null) {
            web.restoreState(savedInstanceState);
        } else {
            web.loadUrl(zielAusIntent(getIntent()));
        }
    }

    /** Ab Android 15 zeichnen Apps unter Status- und Navigationsleiste. Hier wird Platz gelassen, auch für die Tastatur. */
    private void abstaendeFuerSystemleisten() {
        wurzel.setOnApplyWindowInsetsListener((v, insets) -> {
            int l, t, r, b;
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets i = insets.getInsets(
                        WindowInsets.Type.systemBars() | WindowInsets.Type.ime() | WindowInsets.Type.displayCutout());
                l = i.left; t = i.top; r = i.right; b = i.bottom;
            } else {
                l = insets.getSystemWindowInsetLeft(); t = insets.getSystemWindowInsetTop();
                r = insets.getSystemWindowInsetRight(); b = insets.getSystemWindowInsetBottom();
            }
            v.setPadding(l, t, r, b);
            return insets;
        });
    }

    private String zielAusIntent(Intent intent) {
        if (intent != null && Intent.ACTION_VIEW.equals(intent.getAction()) && intent.getData() != null
                && istUnsereSeite(intent.getData())) {
            return intent.getData().toString();
        }
        return START;
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (intent != null && intent.getData() != null && istUnsereSeite(intent.getData())) {
            web.loadUrl(intent.getData().toString());
        }
    }

    static boolean istUnsereSeite(Uri u) {
        return u != null && "https".equals(u.getScheme()) && HOST.equalsIgnoreCase(u.getHost())
                && u.getPath() != null && u.getPath().startsWith(PFAD);
    }

    /** Supabase (Login, Daten, signierte Videolinks) bleibt in der App, damit Downloads hier landen. */
    static boolean bleibtInApp(Uri u) {
        if (u == null || u.getHost() == null) return false;
        String h = u.getHost().toLowerCase();
        return istUnsereSeite(u) || h.endsWith(".supabase.co") || "about".equals(u.getScheme());
    }

    private void extern(Uri u) {
        try {
            Intent i = new Intent(Intent.ACTION_VIEW, u);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        } catch (ActivityNotFoundException e) {
            Toast.makeText(this, "Keine App für diesen Link gefunden", Toast.LENGTH_SHORT).show();
        }
    }

    private void herunterladen(String url, String userAgent, String contentDisposition, String mimeType) {
        String name = URLUtil.guessFileName(url, contentDisposition, mimeType);
        try {
            DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
            r.setMimeType(mimeType);
            r.addRequestHeader("User-Agent", userAgent);
            String cookies = CookieManager.getInstance().getCookie(url);
            if (cookies != null) r.addRequestHeader("Cookie", cookies);
            r.setTitle(name);
            r.setDescription("Clipwreck");
            r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "Clipwreck/" + name);
            DownloadManager dm = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            dm.enqueue(r);
            Toast.makeText(this, "Lädt: Downloads/Clipwreck/" + name, Toast.LENGTH_LONG).show();
        } catch (Exception e) {
            // Älteres Android ohne Speicherrecht oder anderer Fehler: dann eben über den Browser.
            extern(Uri.parse(url));
        }
    }

    private class Client extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri u = request.getUrl();
            if (bleibtInApp(u)) return false;
            extern(u);
            return true;
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (request.isForMainFrame()) {
                String html = "<html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head>"
                        + "<body style='background:#0c0b0e;color:#eee;font-family:sans-serif;display:flex;flex-direction:column;"
                        + "align-items:center;justify-content:center;height:90vh;text-align:center;padding:20px'>"
                        + "<h2>Kein Internet</h2><p>Die Clipwreck-Seite ist gerade nicht erreichbar.</p>"
                        + "<button onclick=\"location.href='" + START + "'\" style='font-size:18px;padding:12px 28px;"
                        + "border:0;border-radius:10px;background:#e11d2e;color:#fff'>Nochmal versuchen</button></body></html>";
                view.loadDataWithBaseURL(START, html, "text/html", "utf-8", null);
            }
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            // Kopieren-Knöpfe: navigator.clipboard ist in Android-WebViews unzuverlässig, deshalb über die App.
            view.evaluateJavascript(
                    "(function(){try{if(!window.ClipwreckApp)return;var c=navigator.clipboard||{};"
                            + "var f=function(t){ClipwreckApp.kopieren(String(t));return Promise.resolve();};"
                            + "try{Object.defineProperty(navigator,'clipboard',{value:Object.assign(Object.create(c),{writeText:f}),configurable:true});}"
                            + "catch(e){c.writeText=f;}}catch(e){}})();", null);
        }
    }

    private class Chrome extends WebChromeClient {
        @Override
        public void onShowCustomView(View view, CustomViewCallback callback) {
            if (vollbildAnsicht != null) { callback.onCustomViewHidden(); return; }
            vollbildAnsicht = view;
            vollbildCallback = callback;
            wurzel.addView(view, new FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
            web.setVisibility(View.GONE);
        }

        @Override
        public void onHideCustomView() {
            if (vollbildAnsicht == null) return;
            wurzel.removeView(vollbildAnsicht);
            vollbildAnsicht = null;
            web.setVisibility(View.VISIBLE);
            if (vollbildCallback != null) vollbildCallback.onCustomViewHidden();
            vollbildCallback = null;
        }
    }

    private class Bruecke {
        @JavascriptInterface
        public void kopieren(String text) {
            runOnUiThread(() -> {
                ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                cm.setPrimaryClip(ClipData.newPlainText("Clipwreck", text));
                if (Build.VERSION.SDK_INT < 33) {
                    Toast.makeText(MainActivity.this, "Kopiert", Toast.LENGTH_SHORT).show();
                }
            });
        }

        @JavascriptInterface
        public String version() {
            return BuildConfigInfo.versionName(MainActivity.this);
        }
    }

    @Override
    public void onBackPressed() {
        if (vollbildAnsicht != null) {
            chrome.onHideCustomView();
        } else if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();
        web.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            wurzel.removeView(web);
            web.destroy();
        }
        super.onDestroy();
    }
}
