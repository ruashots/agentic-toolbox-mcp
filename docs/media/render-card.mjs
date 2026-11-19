// Renders /app/social-card.html to /app/social-card.png (1280 x 640) with the
// toolbox's own browser. Run it inside tb-gateway; the commands are in
// docs/operations.md, "Making the README images".
// (/app, not /tmp: /tmp is RAM-only in the container, and docker cp cannot reach it.)
import { readdirSync, readFileSync, realpathSync } from "node:fs";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

// camoufox-js is a dependency of the browser server, not a top-level package.
const server = realpathSync("/opt/camoufox-mcp/node_modules/camoufox-mcp-server/package.json");
const { Camoufox } = await import(pathToFileURL(createRequire(server).resolve("camoufox-js")).href);

// The card's two fonts come from the browser's own font bundle, embedded as web
// fonts, so the image looks the same on every machine.
const fontDir = "/root/.cache/camoufox/fonts/L";
const font = (suffix) => {
  const file = readdirSync(fontDir).find((f) => f.endsWith(`__${suffix}`));
  return `url(data:font/ttf;base64,${readFileSync(`${fontDir}/${file}`).toString("base64")})`;
};
const fonts = `<style>
@font-face { font-family: "Card Sans"; src: ${font("UbuntuSans[wdth,wght].ttf")}; font-weight: 100 900; }
@font-face { font-family: "Card Mono"; src: ${font("UbuntuSansMono[wght].ttf")}; font-weight: 100 900; }
</style>`;

const html = readFileSync("/app/social-card.html", "utf8").replace("</head>", `${fonts}</head>`);
const browser = await Camoufox({ headless: true, os: "linux", humanize: false });
const page = await browser.newPage({ viewport: { width: 1280, height: 640 } });
await page.setContent(html, { waitUntil: "load" });
await page.evaluate(() => document.fonts.ready);
await page.screenshot({ path: "/app/social-card.png", clip: { x: 0, y: 0, width: 1280, height: 640 } });
await browser.close();
console.log("wrote /app/social-card.png");
