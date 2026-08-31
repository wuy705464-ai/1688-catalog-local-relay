import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

function option(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || !process.argv[index + 1]) throw new Error(`Missing ${name}`);
  return process.argv[index + 1];
}

const inputPath = option("--input");
const templatePath = option("--template");
const outputPath = option("--out");
const previewDir = process.argv.includes("--preview-dir") ? option("--preview-dir") : `${outputPath}.preview`;
const rows = JSON.parse(await fs.readFile(inputPath, "utf8"));
if (!Array.isArray(rows) || rows.length === 0) throw new Error("No prepared customer-catalog rows");
for (const row of rows) {
  if (!row.offer_id || !row.record_hash || !row.collage_path) throw new Error("A prepared row is missing identity or image evidence");
  if (!Array.isArray(row.images) || row.images.length !== 3 || row.images[0].role !== "white_background_product") {
    throw new Error(`Image role contract failed for ${row.offer_id}`);
  }
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.mkdir(previewDir, { recursive: true });
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(templatePath));
const catalog = workbook.worksheets.getItem("Product Catalog");
const guide = workbook.worksheets.getItem("How to Use");
const startRow = 3;
const lastRow = startRow + rows.length - 1;

catalog.deleteAllDrawings();
catalog.getRange("A5:S5").copyTo(catalog.getRange("A3:S3"), "all");
for (let row = 335; row <= lastRow; row += 1) {
  const source = row % 2 === 0 ? catalog.getRange("A4:S4") : catalog.getRange("A5:S5");
  source.copyTo(catalog.getRange(`A${row}:S${row}`), "all");
}
for (let row = startRow; row <= Math.max(lastRow, 334); row += 1) {
  catalog.getRange(`A${row}:S${row}`).clear({ applyTo: "contents" });
}

catalog.getRange(`A${startRow}:S${lastRow}`).values = rows.map((item) => [
  item.number,
  item.category_en || "Jewelry & Accessories",
  `1688-${item.offer_id}`,
  null,
  null,
  item.key_features_en || "",
  item.price_display || "",
  null, null, null,
  item.material_en || "",
  item.size_en || "",
  item.weight_en || "",
  item.color_en || "",
  null, null, null, null, null,
]);
catalog.getRange(`A${startRow}:S${lastRow}`).format.rowHeight = 125;
catalog.getRange(`E${startRow}:E${lastRow}`).format.columnWidthPx = 310;
catalog.getRange(`B${startRow}:D${lastRow}`).format.wrapText = true;
catalog.getRange(`F${startRow}:N${lastRow}`).format.wrapText = true;
catalog.freezePanes.unfreeze();
catalog.freezePanes.freezeRows(2);
catalog.showGridLines = false;

guide.getRange("B13").values = [["Product names are intentionally blank for this same-factory catalog. The SKU (1688 product ID) is the unique product reference."]];
guide.getRange("B14").values = [["Each SKU contains three verified images: a white-background product cover followed by the best available worn or complementary product views. Images and text are bound by SKU and record version."]];

for (let index = 0; index < rows.length; index += 1) {
  const item = rows[index];
  const bytes = await fs.readFile(item.collage_path);
  catalog.images.add({
    dataUrl: `data:image/jpeg;base64,${bytes.toString("base64")}`,
    anchor: { from: { row: startRow - 1 + index, col: 4 }, extent: { widthPx: 270, heightPx: 135 } },
  });
}

const top = await workbook.inspect({ kind: "table", range: "Product Catalog!A1:N8", include: "values,formulas", tableMaxRows: 8, tableMaxCols: 14, maxChars: 9000 });
console.log(top.ndjson);
const tail = await workbook.inspect({ kind: "table", range: `Product Catalog!A${Math.max(startRow, lastRow - 2)}:N${lastRow}`, include: "values,formulas", tableMaxRows: 4, tableMaxCols: 14, maxChars: 7000 });
console.log(tail.ndjson);
const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 300 }, summary: "formula error scan" });
console.log(errors.ndjson);
const drawings = await workbook.inspect({ kind: "drawing", sheetId: "Product Catalog", maxChars: 3000, options: { maxResults: 10 } });
console.log(drawings.ndjson);
for (const [name, sheetName, range] of [
  ["catalog-top.png", "Product Catalog", "A1:N8"],
  ["catalog-tail.png", "Product Catalog", `A${Math.max(startRow, lastRow - 2)}:N${lastRow}`],
  ["guide.png", "How to Use", "A1:B26"],
]) {
  const image = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(previewDir, name), new Uint8Array(await image.arrayBuffer()));
}
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({ outputPath, products: rows.length, lastRow }));
