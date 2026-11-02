import { configState } from "./settings-state.js";
window.MemeSettings = configState;
import { createDialogs } from "./config-modules/dialogs.js";
import { createWorkflowView } from "./config-modules/workflowView.js";
import { createValidation } from "./config-modules/validation.js";
import { createFields } from "./config-modules/fields.js";
import { createPersistence } from "./config-modules/persistence.js";
import { createSetup } from "./config-modules/setup.js";
import { createWorkflow } from "./config-modules/workflow.js";
import { createEvents } from "./config-modules/events.js";

async function initPluginConfig() {
  const app = { state: {}, dom: {} };
  app.dialogs = createDialogs(app);
  app.workflowView = createWorkflowView(app);
  app.validation = createValidation(app);
  app.fields = createFields(app);
  app.persistence = createPersistence(app);
  app.setup = createSetup(app);
  app.workflow = createWorkflow(app);
  app.events = createEvents(app);
  await app.setup.initialize0();
  await app.workflow.initialize1();
  await app.persistence.initialize2();
  await app.events.initialize3();
}


void initPluginConfig().catch((error) => window.MemeUI.showPageError(error));
