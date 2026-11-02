import { createStatus } from "./modules/status.js";
import { createControls } from "./modules/controls.js";
import { createMetrics } from "./modules/metrics.js";
import { createInbox } from "./modules/inbox.js";
import { createPreview } from "./modules/preview.js";
import { createRecords } from "./modules/records.js";
import { createQueries } from "./modules/queries.js";
import { createActions } from "./modules/actions.js";
import { createSetup } from "./modules/setup.js";
import { createEvents } from "./modules/events.js";

async function initSemanticPage() {
  const app = { state: {}, dom: {} };
  app.status = createStatus(app);
  app.controls = createControls(app);
  app.metrics = createMetrics(app);
  app.inbox = createInbox(app);
  app.preview = createPreview(app);
  app.records = createRecords(app);
  app.queries = createQueries(app);
  app.actions = createActions(app);
  app.setup = createSetup(app);
  app.events = createEvents(app);
  await app.setup.initialize0();
  await app.records.initialize1();
  await app.actions.initialize2();
  await app.events.initialize3();
}


initSemanticPage().catch((error) => {
  const message = error?.message || String(error);
  const notice = document.querySelector("#notice");
  if (notice) {
    notice.textContent = message;
    notice.classList.add("error");
  }
});
