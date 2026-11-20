"""Build-time edits to fetch_page's browser (Crawl4AI's Chromium).

1. Drop the Chromium switches that turned the browser's own protections off:
   --disable-web-security let a script on any fetched page read the answers of any
   other site, including services on your network and the toolbox's own services,
   and send them anywhere. --allow-insecure-localhost accepted bad certificates on
   localhost. fetch_page still opens any address an agent gives it, on your network
   or not; the browser only stops one site's scripts from reading another site.
2. Stop the browser's own calls to Google. Measured with Chromium 145 in this image:
   at start it asked accounts.google.com, www.google.com, clients2.google.com (clock),
   android.clients.google.com and mtalk.google.com (push messaging), and for pages
   with a form it sent the form's fingerprint to content-autofill.googleapis.com.
   Playwright's own --disable-features list also lost its effect, because Crawl4AI
   passes a second --disable-features and Chromium keeps only the last one; the list
   below holds both, and the build fails if Playwright's list grows past it.
3. Give each /md call a browser context of its own: no cookies or storage left over
   from other calls, closed when the call ends.
4. Read pages that draw themselves with JavaScript. Such a page (a web app on your
   network, for example) can look empty when Crawl4AI first reads it, and its
   anti-bot check then calls it a block page ("Structural: script_heavy_shell").
   The call now reads it once more, waiting up to 10 seconds for the page to show text.
5. Write supervisord's pid file to /tmp, so the container can run on a read-only disk.

Each edit checks the upstream text first; the build fails if it is not there.
"""
import re
import sys

import yaml

PLAYWRIGHT_SWITCHES = "/usr/local/lib/python3.12/site-packages/playwright/driver/package/lib/server/chromium/chromiumSwitches.js"
UPSTREAM_EXTRA_ARGS = [
    "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-software-rasterizer",
    "--disable-web-security", "--allow-insecure-localhost", "--ignore-certificate-errors",
]
DISABLED_FEATURES = [
    # Playwright 1.58's own list (it is overridden by Crawl4AI's later flag otherwise)
    "AvoidUnnecessaryBeforeUnloadCheckSync", "BoundaryEventDispatchTracksNodeRemoval",
    "DestroyProfileOnBrowserClose", "DialMediaRouteProvider", "GlobalMediaControls",
    "HttpsUpgrades", "LensOverlay", "MediaRouter", "PaintHolding",
    "ThirdPartyStoragePartitioning", "Translate", "AutoDeElevate", "RenderDocument",
    "OptimizationHints",
    # the browser's own calls to Google
    "NetworkTimeServiceQuerying",    # clients2.google.com/time
    "AutofillServerCommunication",   # content-autofill.googleapis.com (form fingerprints)
    "PreconnectToSearch",            # www.google.com
]
# Chromium's Google sign-in, push messaging and search services, pointed at an address
# where nothing answers.
NOWHERE = "http://127.0.0.1:9"
EXTRA_ARGS = [
    "--no-sandbox",                  # the container is the boundary; see docs/operations.md
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--ignore-certificate-errors",   # self-signed certificates are common on home networks
    "--disable-features=" + ",".join(DISABLED_FEATURES),
    "--gaia-url=" + NOWHERE,
    "--google-base-url=" + NOWHERE,
    "--gcm-checkin-url=" + NOWHERE,
    "--gcm-registration-url=" + NOWHERE,
    "--gcm-mcs-endpoint=" + NOWHERE,
]


def edit(path, old, new):
    text = open(path).read()
    if text.count(old) != 1:
        sys.exit(f"patch-crawl4ai: expected text not found exactly once in {path}:\n{old}")
    open(path, "w").write(text.replace(old, new))


# 1-2: browser switches in config.yml, and the same defaults in utils.py
config = yaml.safe_load(open("/app/config.yml"))
browser = config["crawler"]["browser"]
if browser.get("extra_args") != UPSTREAM_EXTRA_ARGS:
    sys.exit(f"patch-crawl4ai: config.yml extra_args changed upstream: {browser.get('extra_args')}")
playwright = re.findall(r'^\s+"(\w+)",?$', open(PLAYWRIGHT_SWITCHES).read().split("disabledFeatures")[1].split("]")[0], re.M)
missing = sorted(set(playwright) - set(DISABLED_FEATURES))
if not playwright or missing:
    sys.exit(f"patch-crawl4ai: add Playwright's disabled features to DISABLED_FEATURES: {missing or 'none found'}")
browser["extra_args"] = EXTRA_ARGS
yaml.safe_dump(config, open("/app/config.yml", "w"), sort_keys=False)
# utils.py carries the same list as a fallback for a config.yml without one.
edit("/app/utils.py", '                "--disable-web-security",\n', "")
edit("/app/utils.py", '                "--allow-insecure-localhost",\n', "")

# 3-4: one browser context per /md call, and a second read for pages drawn by JavaScript
edit("/app/api.py",
     """        crawler = await get_crawler(browser_cfg)
        result = await crawler.arun(
            url=decoded_url,
            config=CrawlerRunConfig(
                markdown_generator=md_generator,
                scraping_strategy=LXMLWebScrapingStrategy(),
                cache_mode=cache_mode
            )
        )
""",
     """        crawler = await get_crawler(browser_cfg)
        # toolbox: a browser context of its own for this call, closed when it ends
        import secrets as _secrets
        _session = "toolbox-" + _secrets.token_hex(16)
        _sessions = [_session]
        try:
            result = await crawler.arun(
                url=decoded_url,
                config=CrawlerRunConfig(
                    markdown_generator=md_generator,
                    scraping_strategy=LXMLWebScrapingStrategy(),
                    cache_mode=cache_mode,
                    session_id=_session,
                )
            )
            # toolbox: a page drawn by JavaScript can look empty before its scripts finish.
            # Read it once more in a fresh context, waiting for the page to show text.
            # (Waiting for the network to go quiet does not work: web apps keep loading
            # files in the background.) Keep the first answer if the second read fails too.
            if not result.success and "Structural:" in (result.error_message or ""):
                _sessions.append("toolbox-" + _secrets.token_hex(16))
                _second = await crawler.arun(
                    url=decoded_url,
                    config=CrawlerRunConfig(
                        markdown_generator=md_generator,
                        scraping_strategy=LXMLWebScrapingStrategy(),
                        cache_mode=cache_mode,
                        session_id=_sessions[-1],
                        wait_for="js:() => document.body && document.body.innerText.trim().length > 200",
                        wait_for_timeout=10000,
                        page_timeout=30000,
                    )
                )
                if _second.success:
                    result = _second
        finally:
            for _s in _sessions:
                try:
                    await crawler.crawler_strategy.browser_manager.kill_session(_s)
                except Exception:
                    pass
""")

# 5: pid file on the RAM disk
edit("/app/supervisord.conf", "[supervisord]\n", "[supervisord]\npidfile=/tmp/supervisord.pid\n")
print("patch-crawl4ai: applied")
