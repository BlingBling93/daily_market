const { chromium } = require("playwright");
const path = require("path");

async function main() {
  const [, , inputHtml, outputPng, width, height, chromePath] = process.argv;
  if (!inputHtml || !outputPng) {
    throw new Error("Usage: screenshot.js <inputHtml> <outputPng> <width> <height> <chromePath>");
  }

  const browser = await chromium.launch({
    headless: true,
    executablePath: chromePath || undefined,
  });
  const page = await browser.newPage({
    viewport: {
      width: Number(width || 1100),
      height: Number(height || 1600),
    },
    deviceScaleFactor: 2,
  });

  await page.goto(`file://${path.resolve(inputHtml)}`, { waitUntil: "networkidle" });
  const card = await page.locator(".card").first();
  await card.screenshot({ path: outputPng });
  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
