"""Stop ad-blocking DNS from failing whole pages in camoufox-mcp-server 2.0.7.

Networks with a DNS filter (AdGuard Home, Pi-hole and similar) answer 0.0.0.0
for tracker and ad hosts; some block lists answer 127.0.0.1. The browser tool's
network guard treats both as private addresses (correct: on Linux 0.0.0.0
reaches localhost) and blocks the request, but it then fails the WHOLE page
because one sub-request was blocked. Measured 2026-11-19 behind such a filter:
7 of 10 common sites (bbc, nytimes, medium, cnn, github, python.org, MDN)
failed this way.

After this patch a sub-request whose host resolves only to 0.0.0.0, ::,
127.0.0.1 or ::1 (the answers a DNS filter gives for a blocked host) is still
blocked and never sent, but silently, and the page loads. A page whose own
address is DNS-blocked still fails, with a clear message. Every other guard
rule is unchanged. The build fails if the upstream code is not exactly what
this patch expects.
"""
import glob, sys

def patch(path, old, new):
    s = open(path).read()
    if s.count(old) != 1:
        sys.exit(f'patch-dns-sinkhole: expected text not found once in {path}')
    open(path, 'w').write(s.replace(old, new))

base = glob.glob('/opt/camoufox-mcp/node_modules/.pnpm/camoufox-mcp-server@2.0.7*/node_modules/camoufox-mcp-server/dist')
if len(base) != 1:
    sys.exit('patch-dns-sinkhole: camoufox-mcp-server 2.0.7 not found')
d = base[0]

patch(d + '/policy.js',
'''    if (records.some((record) => isBlockedIp(record.address))) {
        throw new Error("URL host resolves to a private, local, or reserved address.");''',
'''    if (records.every((record) => ["0.0.0.0", "::", "127.0.0.1", "::1"].includes(record.address))) {
        throw new Error("DNS_BLOCKED: URL host is blocked by the network's DNS filter (resolves to 0.0.0.0 or 127.0.0.1)."); /* toolbox patch */
    }
    if (records.some((record) => isBlockedIp(record.address))) {
        throw new Error("URL host resolves to a private, local, or reserved address.");''')

patch(d + '/browser-runtime.js',
'''        try {
            await validateBrowserRequestUrl(requestUrl);
        }
        catch (requestError) {
            blockRequest(requestUrl, describeError(requestError));
            await route.abort("blockedbyclient").catch(() => undefined);
            return;
        }
        await route.continue()''',
'''        try {
            await validateBrowserRequestUrl(requestUrl);
        }
        catch (requestError) {
            /* toolbox patch: a DNS-filtered sub-request is blocked without failing the page */
            if (!String(requestError && requestError.message).startsWith("DNS_BLOCKED:")) {
                blockRequest(requestUrl, describeError(requestError));
            }
            await route.abort("blockedbyclient").catch(() => undefined);
            return;
        }
        await route.continue()''')
print('patched', d)
