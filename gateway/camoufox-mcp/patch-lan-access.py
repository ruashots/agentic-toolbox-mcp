"""Let the cf_* browser tools open addresses on your own network (camoufox-mcp-server 2.0.7).

camoufox-mcp refuses every private address, so an agent could not use the stealth
browser on the services of its own network: a router page, a NAS, a dashboard.
After this patch:

- An address an agent gives the browser (cf_browse and the other one-shot tools,
  cf_browse_session_navigate) may be on your network: 10.0.0.0/8, 172.16.0.0/12,
  192.168.0.0/16, 100.64.0.0/10 (Tailscale and similar), fc00::/7, or a *.local name.
- Requests a page makes may reach your network only when that page is itself on your
  network: a LAN page loading its own files, a LAN page linking to another LAN page,
  or a redirect from one LAN address to another. A page from the internet still
  cannot make the browser send anything to your network, by sub-request, frame,
  redirect or navigation.
- Loopback (127.0.0.0/8, ::1, localhost), 0.0.0.0, link-local, multicast and the
  other reserved ranges stay refused for everyone.

Every other guard rule is unchanged. The build fails if the upstream code is not
exactly what this patch expects.
"""
import glob
import sys


def patch(path, old, new, count=1):
    s = open(path).read()
    if s.count(old) != count:
        sys.exit(f"patch-lan-access: expected text not found {count} time(s) in {path}:\n{old}")
    open(path, "w").write(s.replace(old, new))


base = glob.glob("/opt/camoufox-mcp/node_modules/.pnpm/camoufox-mcp-server@2.0.7*/node_modules/camoufox-mcp-server/dist")
if len(base) != 1:
    sys.exit("patch-lan-access: camoufox-mcp-server 2.0.7 not found")
d = base[0]

# --- policy.js: an allowLan option on the URL checks --------------------------------
policy = f"{d}/policy.js"
patch(policy, "export function parseAndValidateTargetUrl(rawUrl) {",
      "export function parseAndValidateTargetUrl(rawUrl, options = {}) {")
patch(policy,
      "    if (isBlockedHostname(hostname)) {\n",
      "    if (isBlockedHostname(hostname) && !(options.allowLan && isLanHostname(hostname))) {\n")
patch(policy,
      "        if (isBlockedIp(hostname)) {\n",
      "        if (isBlockedIp(hostname) && !(options.allowLan && isLanIp(hostname))) {\n")
patch(policy, "export async function validateTargetUrl(rawUrl) {\n    const { parsed, hostname, needsDnsCheck } = parseAndValidateTargetUrl(rawUrl);",
      "export async function validateTargetUrl(rawUrl, options = {}) {\n    const { parsed, hostname, needsDnsCheck } = parseAndValidateTargetUrl(rawUrl, options);")
patch(policy,
      "    if (records.some((record) => isBlockedIp(record.address))) {\n",
      "    if (records.some((record) => isBlockedIp(record.address) && !(options.allowLan && isLanIp(record.address)))) {\n")
patch(policy, "export function parseAndValidateBrowserRequestUrl(rawUrl) {\n    return parseAndValidateTargetUrl(browserRequestPolicyUrl(rawUrl));",
      "export function parseAndValidateBrowserRequestUrl(rawUrl, options = {}) {\n    return parseAndValidateTargetUrl(browserRequestPolicyUrl(rawUrl), options);")
patch(policy, "export async function validateBrowserRequestUrl(rawUrl) {\n    return validateTargetUrl(browserRequestPolicyUrl(rawUrl));",
      "export async function validateBrowserRequestUrl(rawUrl, options = {}) {\n    return validateTargetUrl(browserRequestPolicyUrl(rawUrl), options);")
with open(policy, "a") as f:
    f.write('''
/* toolbox patch: addresses on your own network */
const LAN_IPV6 = buildIpv6Cidr("fc00::", 7);
export function isLanIp(address) {
    const normalized = normalizeHostname(address);
    const ipv4 = ipv4FromMappedIpv6(normalized) || (isIP(normalized) === 4 ? normalized : undefined);
    if (ipv4) {
        const [first, second] = ipv4.split(".").map((part) => Number.parseInt(part, 10));
        return first === 10
            || (first === 172 && second >= 16 && second <= 31)
            || (first === 192 && second === 168)
            || (first === 100 && second >= 64 && second <= 127);
    }
    if (isIP(normalized) === 6) {
        const value = parseIpv6ToBigInt(normalized);
        return value !== undefined && isIpv6InCidr(value, LAN_IPV6);
    }
    return false;
}
export function isLanHostname(hostname) {
    return normalizeHostname(hostname).endsWith(".local");
}
''')

# --- browser-runtime.js: who may reach your network ---------------------------------
runtime = f"{d}/browser-runtime.js"
patch(runtime,
      'import { parseAndValidateBrowserRequestUrl, validateBrowserRequestUrl, validateTargetUrl } from "./policy.js";\n',
      'import { parseAndValidateBrowserRequestUrl, validateBrowserRequestUrl, validateTargetUrl, isLanIp, isLanHostname, normalizeHostname } from "./policy.js";\n'
      'import { lookup as lanLookup } from "node:dns/promises";\n'
      'import { isIP as lanIsIP } from "node:net";\n')
patch(runtime,
      "export async function installRequestGuard(context) {\n    let inspectedRequests = 0;\n    let blockedRequestError;\n",
      """export async function installRequestGuard(context) {
    let inspectedRequests = 0;
    let blockedRequestError;
    /* toolbox patch: addresses on your network that the agent itself asked for */
    const agentLanHosts = new Set();
    const agentLanUrls = new Set();
    function withoutHash(rawUrl) {
        try {
            const parsed = new URL(rawUrl);
            parsed.hash = "";
            return parsed.toString();
        }
        catch {
            return rawUrl;
        }
    }
    function hostOf(rawUrl) {
        try {
            return normalizeHostname(new URL(rawUrl).hostname);
        }
        catch {
            return "";
        }
    }
    function isLanPage(rawUrl) {
        const host = hostOf(rawUrl);
        return agentLanHosts.has(host) || (lanIsIP(host) !== 0 && isLanIp(host));
    }
    function lanAllowedFor(request) {
        try {
            const previous = request.redirectedFrom();
            if (previous) {
                return isLanPage(previous.url());
            }
            let frame;
            try {
                frame = request.frame();
            }
            catch {
                return false;
            }
            if (request.isNavigationRequest()) {
                const parent = frame.parentFrame();
                if (parent) {
                    return isLanPage(parent.url());
                }
                return agentLanUrls.has(withoutHash(request.url())) || isLanPage(frame.url());
            }
            return isLanPage(frame.url());
        }
        catch {
            return false;
        }
    }
""")
patch(runtime,
      "            parseAndValidateBrowserRequestUrl(requestUrl);\n        }\n        catch (requestError) {\n            blockRequest(requestUrl, describeError(requestError));\n        }\n    });\n",
      "            parseAndValidateBrowserRequestUrl(requestUrl, { allowLan: lanAllowedFor(request) });\n        }\n        catch (requestError) {\n            blockRequest(requestUrl, describeError(requestError));\n        }\n    });\n")
patch(runtime,
      "            await validateBrowserRequestUrl(requestUrl);\n        }\n        catch (requestError) {\n            /* toolbox patch: a DNS-filtered",
      "            await validateBrowserRequestUrl(requestUrl, { allowLan: lanAllowedFor(route.request()) });\n        }\n        catch (requestError) {\n            /* toolbox patch: a DNS-filtered")
patch(runtime,
      "            await validateBrowserRequestUrl(requestUrl);\n        }\n        catch (requestError) {\n            blockRequest(requestUrl, describeError(requestError));\n            await webSocket.close(",
      "            await validateBrowserRequestUrl(requestUrl, { allowLan: agentLanHosts.has(hostOf(requestUrl)) });\n        }\n        catch (requestError) {\n            blockRequest(requestUrl, describeError(requestError));\n            await webSocket.close(")
patch(runtime,
      "                try {\n                    parseAndValidateBrowserRequestUrl(requestUrl);\n                }\n",
      "                try {\n                    parseAndValidateBrowserRequestUrl(requestUrl, { allowLan: isLanPage(page.url()) || agentLanHosts.has(hostOf(requestUrl)) });\n                }\n")
patch(runtime,
      "    return {\n        assertAllowed() {\n",
      """    return {
        async approveAgentUrl(rawUrl) {
            const host = hostOf(String(rawUrl));
            let lan = false;
            if (lanIsIP(host) !== 0) {
                lan = isLanIp(host);
            }
            else if (isLanHostname(host)) {
                lan = true;
            }
            else if (host) {
                try {
                    const records = await lanLookup(host, { all: true, verbatim: true });
                    lan = records.length > 0 && records.every((record) => isLanIp(record.address));
                }
                catch {
                    lan = false;
                }
            }
            if (lan) {
                agentLanHosts.add(host);
                agentLanUrls.add(withoutHash(String(rawUrl)));
            }
        },
        assertAllowed() {
""")
patch(runtime,
      "            requestGuard.watchPage(page);\n            const rawUrls = [effectiveInput.url,",
      "            requestGuard.watchPage(page);\n            await requestGuard.approveAgentUrl(targetUrl.toString());\n            const rawUrls = [effectiveInput.url,")
patch(runtime, "await validateTargetUrl(page.url());", "await validateTargetUrl(page.url(), { allowLan: true });", count=2)

# --- browser-options.js and sessions.js: the agent's own URLs -------------------------
patch(f"{d}/browser-options.js",
      "    const targetUrl = await validateTargetUrl(input.url);\n",
      "    const targetUrl = await validateTargetUrl(input.url, { allowLan: true });\n")
patch(f"{d}/sessions.js",
      "    const targetUrl = await validateTargetUrl(url);\n    session.rawUrls.push(url);\n",
      "    const targetUrl = await validateTargetUrl(url, { allowLan: true });\n    session.rawUrls.push(url);\n"
      "    await session.requestGuard.approveAgentUrl(targetUrl.toString());\n")
print("patch-lan-access: applied")
