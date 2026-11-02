import { createApi } from "./modules/api.js";
import { createStatus } from "./modules/status.js";
import { createExport } from "./modules/export.js";
import { createImport } from "./modules/import.js";
import { createRuleModel } from "./modules/ruleModel.js";
import { createRuleView } from "./modules/ruleView.js";
import { createRuleIO } from "./modules/ruleIO.js";
import { createBackup } from "./modules/backup.js";
import { createSetup } from "./modules/setup.js";
import { createEvents } from "./modules/events.js";

async function initSettingsPage() {
  const app = { state: {}, dom: {} };
  app.api = createApi(app);
  app.status = createStatus(app);
  app.export = createExport(app);
  app.import = createImport(app);
  app.ruleModel = createRuleModel(app);
  app.ruleView = createRuleView(app);
  app.ruleIO = createRuleIO(app);
  app.backup = createBackup(app);
  app.setup = createSetup(app);
  app.events = createEvents(app);
  await app.setup.initialize0();
  await app.events.initialize1();
}


void initSettingsPage().catch((error) => window.MemeUI.showPageError(error));
