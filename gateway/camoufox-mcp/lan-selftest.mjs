// Build-time proof of the cf_* tools' rule for your own network (patch-lan-access.py).
// Runs the real, patched request guard against made-up requests and fails the build
// if any answer is wrong. IP literals only, so no DNS lookup is involved.
import { installRequestGuard } from "./dist/browser-runtime.js";

const LAN = "http://192.168.77.10/";
const LAN_ASSET = "http://10.9.8.7/app.js";
const INTERNET = "http://93.184.215.14/";

function frame(url, parent = null) {
    return { url: () => url, parentFrame: () => parent };
}
function request(url, { from = null, inFrame = frame("about:blank"), navigation = false } = {}) {
    return {
        url: () => url,
        redirectedFrom: () => (from ? request(from) : null),
        frame: () => inFrame,
        isNavigationRequest: () => navigation,
    };
}
async function decide(req, approve = []) {
    const handlers = {};
    const context = {
        on(event, fn) { handlers[event] = fn; },
        async route(_pattern, fn) { handlers.route = fn; },
        async routeWebSocket(_pattern, fn) { handlers.ws = fn; },
    };
    const guard = await installRequestGuard(context);
    for (const url of approve) {
        await guard.approveAgentUrl(url);
    }
    let outcome = "none";
    await handlers.route({
        request: () => req,
        abort: async () => { outcome = "blocked"; },
        continue: async () => { outcome = "sent"; },
    });
    return outcome;
}

const cases = [
    ["the agent opens a LAN address", request(LAN, { navigation: true }), [LAN], "sent"],
    ["a LAN address nobody asked for", request(LAN, { navigation: true }), [], "blocked"],
    ["a LAN page loads a file from another LAN host", request(LAN_ASSET, { inFrame: frame(LAN) }), [LAN], "sent"],
    ["a LAN page reached by IP loads a LAN file", request(LAN_ASSET, { inFrame: frame("http://192.168.77.11/") }), [], "sent"],
    ["an internet page asks for a LAN file", request(LAN_ASSET, { inFrame: frame(INTERNET) }), [LAN], "blocked"],
    ["an internet page redirects to a LAN address", request(LAN, { from: INTERNET, navigation: true }), [], "blocked"],
    ["a LAN address redirects to another LAN address", request(LAN_ASSET, { from: LAN, navigation: true }), [LAN], "sent"],
    ["an internet page frames a LAN address", request(LAN, { navigation: true, inFrame: frame("about:blank", frame(INTERNET)) }), [LAN], "blocked"],
    ["an internet page navigates itself to a LAN address", request(LAN_ASSET, { navigation: true, inFrame: frame(INTERNET) }), [LAN], "blocked"],
    ["an internet page loads an internet file", request("http://93.184.215.15/x.js", { inFrame: frame(INTERNET) }), [], "sent"],
    ["a LAN page asks for loopback", request("http://127.0.0.1:8000/mcp", { inFrame: frame(LAN) }), [LAN], "blocked"],
    ["a LAN page asks for link-local", request("http://169.254.169.254/", { inFrame: frame(LAN) }), [LAN], "blocked"],
    ["an internet page links to iana.org (192.0.43.8)", request("http://192.0.43.8/", { navigation: true, inFrame: frame(INTERNET) }), [], "sent"],
    ["a page asks for the 192.0.2.0/24 documentation range", request("http://192.0.2.10/", { inFrame: frame(INTERNET) }), [], "blocked"],
];

let failed = 0;
for (const [name, req, approve, expected] of cases) {
    const got = await decide(req, approve);
    const ok = got === expected;
    if (!ok) failed += 1;
    console.log(`${ok ? "ok  " : "FAIL"} ${name}: ${got}${ok ? "" : ` (expected ${expected})`}`);
}
if (failed) {
    console.error(`lan-selftest: ${failed} case(s) wrong`);
    process.exit(1);
}
console.log("lan-selftest: the LAN rule holds");
process.exit(0);
