import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

function option(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || !process.argv[index + 1]) throw new Error(`Missing ${name}`);
  return process.argv[index + 1];
}

function displayItemNo(value) {
  const itemNo = String(value || "");
  return /^\d+$/.test(itemNo) ? `Ref-${itemNo}` : itemNo;
}

const inputPath = option("--input");
const templatePath = option("--template");
const outputPath = option("--out");
const previewDir = process.argv.includes("--preview-dir") ? option("--preview-dir") : `${outputPath}.preview`;
const rows = JSON.parse(await fs.readFile(inputPath, "utf8"));
if (!Array.isArray(rows) || rows.length === 0) throw new Error("No catalog rows supplied");
for (const row of rows) {
  if (!row.offer_id || !row.record_hash || !row.collage_path || !Array.isArray(row.images) || row.images.length !== 3) {
    throw new Error("A row is missing identity or approved image evidence");
  }
  if (row.images[0].role !== "white_background_product") {
    throw new Error(`Image role contract failed for ${row.offer_id}`);
  }
  if (!row.color_en || !row.size_en || String(row.key_features_en || "").split("\n").length < 9) {
    throw new Error(`Required customer fields are incomplete for ${row.offer_id}`);
  }
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.mkdir(previewDir, { recursive: true });
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(templatePath));
const catalog = workbook.worksheets.getItem("Product Catalog");
const guide = workbook.worksheets.getItem("How to Use");
const startRow = 3;
const lastDataRow = startRow + rows.length - 1;
const groups = new Map();
for (const row of rows) groups.set(row.category_en, [...(groups.get(row.category_en) || []), row]);
const categoryTabs = [
  ["Charms & Pendants", "饰品配饰"],
  ["Necklaces & Pendants", "项链"],
  ["Bracelets", "手链"],
  ["Rings", "戒指"],
  ["Earrings", "耳环"],
  ["Brooches", "胸针"],
  ["Anklets", "脚链"],
  ["Bangles & Bracelets", "手镯"],
  ["Jewelry Sets", "首饰套装"],
  ["Beading Components", "串珠配件"],
  ["Hair Accessories", "发饰"],
];
const summaryRows = [...groups.values()].reduce((total, group) => total + group.length + 2, 0);
const summaryStart = lastDataRow + 3;
const finalRow = summaryStart + summaryRows;

for (let row = 333; row <= finalRow; row += 1) {
  catalog.getRange("A3:S3").copyTo(catalog.getRange(`A${row}:S${row}`), "all");
}
catalog.getRange(`A${startRow}:S${finalRow}`).clear({ applyTo: "contents" });
catalog.deleteAllDrawings();

catalog.getRange(`A${startRow}:S${lastDataRow}`).values = rows.map((item) => [
  item.number,
  item.category_en,
  item.sku,
  null,
  null,
  item.key_features_en,
  item.price_display,
  null,
  null,
  item.price_display.includes("Tiered Price") ? "Tiered pricing available" : "",
  item.material_en,
  item.size_en,
  item.weight_en || "",
  item.color_en,
  item.style_en,
  item.occasion_en,
  null,
  null,
  null,
]);
catalog.getRange(`A${startRow}:S${lastDataRow}`).format.rowHeight = 168;
catalog.getRange(`A${startRow}:S${lastDataRow}`).format.borders = {
  preset: "all",
  style: "thin",
  color: "#D9D9D9",
};
catalog.getRange(`F${startRow}:F${lastDataRow}`).format = {
  wrapText: true,
  horizontalAlignment: "left",
  verticalAlignment: "center",
  font: { size: 9 },
};
catalog.getRange(`B${startRow}:S${lastDataRow}`).format.wrapText = true;
catalog.getRange(`B${startRow}:S${lastDataRow}`).format.verticalAlignment = "center";
catalog.getRange(`A${startRow}:C${lastDataRow}`).format.horizontalAlignment = "center";
catalog.getRange(`G${startRow}:S${lastDataRow}`).format.horizontalAlignment = "center";
catalog.getRange(`E${startRow}:E${lastDataRow}`).format.columnWidthPx = 420;
catalog.getRange(`A${startRow}:S${lastDataRow}`).format.borders = {
  preset: "all",
  style: "thin",
  color: "#D9D9D9",
};

for (let index = 0; index < rows.length; index += 1) {
  const imageBytes = await fs.readFile(rows[index].collage_path);
  catalog.images.add({
    dataUrl: `data:image/jpeg;base64,${imageBytes.toString("base64")}`,
    anchor: { from: { row: startRow - 1 + index, col: 4 }, extent: { widthPx: 360, heightPx: 180 } },
  });
}

let rowCursor = summaryStart;
for (const [category, group] of [...groups.entries()].sort(([left], [right]) => left.localeCompare(right))) {
  catalog.getRange(`A${rowCursor}:G${rowCursor}`).values = [[`${category} — ${group.length} Products`, null, null, null, null, null, null]];
  catalog.getRange(`A${rowCursor}:G${rowCursor}`).format = {
    fill: "#1F4E78",
    font: { bold: true, color: "#FFFFFF", size: 11 },
    horizontalAlignment: "left",
    verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: "#1F4E78" },
  };
  catalog.getRange(`A${rowCursor}:G${rowCursor}`).format.rowHeight = 23;
  rowCursor += 1;
  catalog.getRange(`A${rowCursor}:G${rowCursor}`).values = [["No.", "SKU", "Item No.", "Material", "Color", "Size", "Applicable Scenarios"]];
  catalog.getRange(`A${rowCursor}:G${rowCursor}`).format = {
    fill: "#D9EAF7",
    font: { bold: true, color: "#17365D", size: 9 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: "#B7C9DA" },
    wrapText: true,
  };
  rowCursor += 1;
  const end = rowCursor + group.length - 1;
  catalog.getRange(`A${rowCursor}:G${end}`).values = group.map((item) => [
    item.number, item.sku, displayItemNo(item.item_no), item.material_en, item.color_en, item.size_en, item.occasion_en,
  ]);
  catalog.getRange(`A${rowCursor}:G${end}`).format = {
    font: { size: 9 },
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#D9E2F3" },
  };
  catalog.getRange(`A${rowCursor}:A${end}`).format.horizontalAlignment = "center";
  catalog.getRange(`B${rowCursor}:G${end}`).format.horizontalAlignment = "left";
  catalog.getRange(`A${rowCursor}:G${end}`).format.rowHeight = 24;
  if (end + 1 <= finalRow) {
    catalog.getRange(`A${end + 1}:G${end + 1}`).format.rowHeight = 8;
  }
  rowCursor = end + 2;
}

const categoryColumnWidthsPx = [
  52, 112, 138, 188, 420, 420, 180, 150, 80, 175,
  112, 190, 105, 185, 130, 210, 150, 150, 150,
];
for (const [category, tabName] of categoryTabs) {
  const group = groups.get(category) || [];
  if (group.length === 0) continue;
  const categorySheet = workbook.worksheets.add(tabName);
  const categoryLastRow = startRow + group.length - 1;

  catalog.getRange("A1:S2").copyTo(categorySheet.getRange("A1:S2"), "all");
  categorySheet.getRange("A1:S1").merge();
  categorySheet.getRange("A1").values = [[`${tabName} / ${category} — ${group.length} Products`]];
  categorySheet.getRange("A1:S1").format = {
    fill: "#1F4E78",
    font: { bold: true, color: "#FFFFFF", size: 14 },
    horizontalAlignment: "left",
    verticalAlignment: "center",
  };
  categorySheet.getRange("A1:S1").format.rowHeight = 30;
  categorySheet.getRange("A2:S2").format = {
    fill: "#1F4E78",
    font: { bold: true, color: "#FFFFFF", size: 11 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#D9E2F3" },
  };
  categorySheet.getRange("A2:S2").format.rowHeight = 54;

  for (let row = startRow; row <= categoryLastRow; row += 1) {
    catalog.getRange("A3:S3").copyTo(categorySheet.getRange(`A${row}:S${row}`), "all");
  }
  categorySheet.getRange(`A${startRow}:S${categoryLastRow}`).clear({ applyTo: "contents" });
  categorySheet.getRange(`A${startRow}:S${categoryLastRow}`).values = group.map((item) => [
    item.number,
    item.category_en,
    item.sku,
    null,
    null,
    item.key_features_en,
    item.price_display,
    null,
    null,
    item.price_display.includes("Tiered Price") ? "Tiered pricing available" : "",
    item.material_en,
    item.size_en,
    item.weight_en || "",
    item.color_en,
    item.style_en,
    item.occasion_en,
    null,
    null,
    null,
  ]);
  categorySheet.getRange(`A${startRow}:S${categoryLastRow}`).format.rowHeight = 168;
  categorySheet.getRange(`A${startRow}:S${categoryLastRow}`).format.borders = {
    preset: "all",
    style: "thin",
    color: "#D9D9D9",
  };
  categorySheet.getRange(`F${startRow}:F${categoryLastRow}`).format = {
    wrapText: true,
    horizontalAlignment: "left",
    verticalAlignment: "center",
    font: { size: 9 },
  };
  categorySheet.getRange(`B${startRow}:S${categoryLastRow}`).format.wrapText = true;
  categorySheet.getRange(`B${startRow}:S${categoryLastRow}`).format.verticalAlignment = "center";
  categorySheet.getRange(`A${startRow}:C${categoryLastRow}`).format.horizontalAlignment = "center";
  categorySheet.getRange(`G${startRow}:S${categoryLastRow}`).format.horizontalAlignment = "center";
  categorySheet.getRange(`A${startRow}:S${categoryLastRow}`).format.borders = {
    preset: "all",
    style: "thin",
    color: "#D9D9D9",
  };
  for (let col = 0; col < categoryColumnWidthsPx.length; col += 1) {
    categorySheet.getRangeByIndexes(0, col, categoryLastRow, 1).format.columnWidthPx = categoryColumnWidthsPx[col];
  }
  for (let index = 0; index < group.length; index += 1) {
    const imageBytes = await fs.readFile(group[index].collage_path);
    categorySheet.images.add({
      dataUrl: `data:image/jpeg;base64,${imageBytes.toString("base64")}`,
      anchor: { from: { row: startRow - 1 + index, col: 4 }, extent: { widthPx: 360, heightPx: 180 } },
    });
  }
  categorySheet.freezePanes.freezeRows(2);
  categorySheet.showGridLines = false;
}

guide.getRange("B13").values = [["Product names are intentionally blank for this same-factory catalog. Use the SKU and Item No. as the product reference."]];
guide.getRange("B14").values = [["Each row contains a white-background cover and two supplementary views. Colors and sizes are source-led where available; otherwise they use a practical catalog-level fallback for buyer reference."]];
catalog.showGridLines = false;

const top = await workbook.inspect({ kind: "table", range: "Product Catalog!A1:S6", include: "values,formulas", tableMaxRows: 6, tableMaxCols: 19, maxChars: 10000 });
console.log(top.ndjson);
const summary = await workbook.inspect({ kind: "table", range: `Product Catalog!A${summaryStart}:G${Math.min(finalRow, summaryStart + 10)}`, include: "values,formulas", tableMaxRows: 12, tableMaxCols: 7, maxChars: 6000 });
console.log(summary.ndjson);
const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 300 }, summary: "formula error scan" });
console.log(errors.ndjson);
const drawings = await workbook.inspect({ kind: "drawing", sheetId: "Product Catalog", maxChars: 2500, options: { maxResults: 8 } });
console.log(drawings.ndjson);
for (const [name, sheetName, range] of [
  ["catalog-top.png", "Product Catalog", "A1:S5"],
  ["category-index.png", "Product Catalog", `A${summaryStart}:G${Math.min(finalRow, summaryStart + 12)}`],
  ["how-to-use.png", "How to Use", "A1:B26"],
]) {
  const image = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(previewDir, name), new Uint8Array(await image.arrayBuffer()));
}
for (const [category, tabName] of categoryTabs) {
  const group = groups.get(category) || [];
  if (group.length === 0) continue;
  const image = await workbook.render({ sheetName: tabName, range: `A1:S${Math.min(group.length + 2, 5)}`, scale: 1, format: "png" });
  await fs.writeFile(path.join(previewDir, `category-${tabName}.png`), new Uint8Array(await image.arrayBuffer()));
}
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({ outputPath, products: rows.length, summaryStart, finalRow, categories: groups.size, categoryTabs: categoryTabs.length }));
