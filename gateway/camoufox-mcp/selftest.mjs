// Build-time proof that the driver, the protocol library and the frozen browser
// work together: launch Camoufox exactly the way the MCP server does and render
// a page. Runs offline (no network during build is needed). If this fails, the
// image build fails and the running toolbox is never replaced.
import { Camoufox } from "camoufox-js";

const browser = await Camoufox({ headless: true });
const page = await browser.newPage();
await page.setContent("<title>selftest</title><p id=m>camoufox selftest ok</p>");
const text = await page.textContent("#m");
await browser.close();
if (text !== "camoufox selftest ok") {
  console.error("camoufox selftest: unexpected page text:", text);
  process.exit(1);
}
console.log("camoufox selftest ok");
