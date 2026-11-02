import { createApi } from "./modules/api.js";
import { createStatus } from "./modules/status.js";
import { createInstall } from "./modules/install.js";
import { createCatalog } from "./modules/catalog.js";
import { createCovers } from "./modules/covers.js";
import { createCards } from "./modules/cards.js";
import { createSetup } from "./modules/setup.js";
import { createEvents } from "./modules/events.js";

async function initCatalogPage() {
  const app = { state: {}, dom: {} };
  app.api = createApi(app);
  app.status = createStatus(app);
  app.install = createInstall(app);
  app.catalog = createCatalog(app);
  app.covers = createCovers(app);
  app.cards = createCards(app);
  app.setup = createSetup(app);
  app.events = createEvents(app);
  await app.setup.initialize0();
  await app.install.initialize1();
  await app.events.initialize2();
}


void initCatalogPage().catch((error) => {
  const status = document.getElementById("catalog-status");
  status.textContent = `页面初始化失败：${
    error?.message || String(error)
  }。请重新打开页面。`;
  status.classList.add("error");
  for (const id of ["official-grid", "community-grid"]) {
    document.getElementById(id).textContent = "资源暂时不可用。";
  }
});
