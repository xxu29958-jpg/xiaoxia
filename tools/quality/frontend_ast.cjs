// Parse supplied source bytes only. Never load application modules or Vite config.
const fs = require("node:fs");
const path = require("node:path").posix;
const { createHash } = require("node:crypto");
const { createRequire } = require("node:module");
const pinned = createRequire(
  require("node:path").resolve(__dirname, "../../quality/package.json"),
);
const ts = pinned("typescript");
const vue = pinned("@vue/compiler-sfc");
if (ts.version !== "5.1.6" || vue.version !== "3.3.4")
  throw new Error("Unpinned frontend parser");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const known = new Set([...Object.keys(input.sources), ...input.resources]);
const printer = ts.createPrinter({ removeComments: true });
const aliases = [];
for (const [filename, source] of Object.entries(input.sources)) {
  if (!/\/vite\.config\.[cm]?[jt]s$/.test(filename)) continue;
  const tree = ts.createSourceFile(
    filename,
    source,
    ts.ScriptTarget.Latest,
    true,
  );
  function visit(node) {
    if (ts.isObjectLiteralExpression(node)) {
      const properties = Object.fromEntries(
        node.properties
          .filter(ts.isPropertyAssignment)
          .map((p) => [p.name.getText(tree), p.initializer]),
      );
      const find = properties.find;
      let name = find && ts.isStringLiteral(find) ? find.text : null;
      const exact =
        find && ts.isRegularExpressionLiteral(find)
          ? find.text.match(/^\/\^([\w-]+)\$\/$/)
          : null;
      if (exact) name = exact[1];
      if (name && properties.replacement) {
        function url(child) {
          if (
            ts.isNewExpression(child) &&
            child.expression.getText(tree) === "URL" &&
            child.arguments?.[0] &&
            ts.isStringLiteral(child.arguments[0])
          ) {
            aliases.push({
              root: path.dirname(filename) + "/",
              name,
              exact: Boolean(exact),
              target: path.normalize(
                path.join(path.dirname(filename), child.arguments[0].text),
              ),
            });
          }
          ts.forEachChild(child, url);
        }
        url(properties.replacement);
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
}

function resolve(source, name) {
  let base;
  const alias = aliases.find(
    (a) =>
      source.startsWith(a.root) &&
      (name === a.name || (!a.exact && name.startsWith(a.name + "/"))),
  );
  if (alias) base = alias.target + name.slice(alias.name.length);
  else if (name.startsWith("."))
    base = path.normalize(path.join(path.dirname(source), name));
  else return { external: name };
  base = base.split("?")[0];
  const choices = [
    base,
    ...[".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".json"].map(
      (e) => base + e,
    ),
    ...["index.ts", "index.js", "index.vue"].map((n) => base + "/" + n),
  ];
  const target = choices.find((p) => known.has(p));
  return target
    ? { target }
    : { gap: { kind: "unresolved_frontend_import", module: name } };
}

function scriptFacts(filename, content, offset, record) {
  const kind = /\.tsx$/.test(filename)
    ? ts.ScriptKind.TSX
    : /\.jsx$/.test(filename)
    ? ts.ScriptKind.JSX
    : ts.ScriptKind.TS;
  const tree = ts.createSourceFile(
    filename,
    content,
    ts.ScriptTarget.Latest,
    true,
    kind,
  );
  const line = (pos) =>
    tree.getLineAndCharacterOfPosition(pos).line + 1 + offset;
  record.parse_errors.push(
    ...tree.parseDiagnostics.map((d) => ({
      line: line(d.start || 0),
      reason: ts.flattenDiagnosticMessageText(d.messageText, "\n"),
    })),
  );
  function reference(name, at) {
    const resolved = resolve(filename.split("#")[0], name);
    if (resolved.target) record.dependencies.push(resolved.target);
    if (resolved.external) record.external_imports.push(resolved.external);
    if (resolved.gap) record.dynamic_gaps.push({ ...resolved.gap, line: at });
  }
  function visit(node) {
    if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier)
    ) {
      reference(node.moduleSpecifier.text, line(node.getStart(tree)));
    }
    if (ts.isCallExpression(node)) {
      const isImport =
        node.expression.kind === ts.SyntaxKind.ImportKeyword ||
        node.expression.getText(tree) === "require";
      if (isImport) {
        if (node.arguments[0] && ts.isStringLiteralLike(node.arguments[0]))
          reference(node.arguments[0].text, line(node.getStart(tree)));
        else
          record.dynamic_gaps.push({
            kind: "computed_frontend_import",
            line: line(node.getStart(tree)),
          });
      }
      if (node.expression.getText(tree) === "import.meta.glob")
        record.dynamic_gaps.push({
          kind: "frontend_glob_registration",
          line: line(node.getStart(tree)),
        });
    }
    if (
      ts.isFunctionDeclaration(node) ||
      ts.isMethodDeclaration(node) ||
      ts.isArrowFunction(node) ||
      ts.isFunctionExpression(node) ||
      ts.isClassDeclaration(node)
    ) {
      const name =
        node.name?.getText(tree) ||
        node.parent?.name?.getText(tree) ||
        `<anonymous:${line(node.getStart(tree))}>`;
      const printed = printer.printNode(ts.EmitHint.Unspecified, node, tree);
      record.frontend_symbols.push({
        name,
        kind: ts.isClassDeclaration(node) ? "class" : "function",
        start: line(node.getStart(tree)),
        end: line(node.end),
        lines: line(node.end) - line(node.getStart(tree)) + 1,
        fingerprint: createHash("sha256").update(printed).digest("hex"),
        complexity: null,
      });
    }
    if (node.modifiers?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword)) {
      record.public_interfaces.push({
        name: node.name?.getText(tree) || "default-or-bindings",
        line: line(node.getStart(tree)),
        kind: ts.SyntaxKind[node.kind],
      });
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
}

function parse(filename, content) {
  const record = {
    dependencies: [],
    external_imports: [],
    dynamic_gaps: [],
    parse_errors: [],
    frontend_symbols: [],
    public_interfaces: [],
  };
  if (!filename.endsWith(".vue")) scriptFacts(filename, content, 0, record);
  else {
    const { descriptor, errors } = vue.parse(content, {
      filename,
      sourceMap: false,
    });
    record.parse_errors.push(
      ...errors.map((e) => ({ reason: String(e.message || e) })),
    );
    for (const block of [descriptor.script, descriptor.scriptSetup]) {
      if (block)
        scriptFacts(
          filename + "#script." + (block.lang || "js"),
          block.content,
          block.loc.start.line - 1,
          record,
        );
    }
    for (const block of [
      descriptor.template,
      descriptor.script,
      descriptor.scriptSetup,
      ...descriptor.styles,
      ...descriptor.customBlocks,
    ]) {
      if (!block?.src) continue;
      const resolved = resolve(filename, block.src);
      if (resolved.target) record.dependencies.push(resolved.target);
      if (resolved.gap) record.dynamic_gaps.push(resolved.gap);
    }
    if (descriptor.template?.lang && descriptor.template.lang !== "html")
      record.dynamic_gaps.push({
        kind: "template_preprocessor",
        language: descriptor.template.lang,
      });
  }
  record.dependencies = [...new Set(record.dependencies)].sort();
  record.external_imports = [...new Set(record.external_imports)].sort();
  return record;
}

process.stdout.write(
  JSON.stringify(
    Object.fromEntries(
      Object.entries(input.sources).map(([name, content]) => [
        name,
        parse(name, content),
      ]),
    ),
  ),
);
