import { openStorageGuide } from "../storage-guide.js";
// dialogs: one page runtime owns shared state and DOM references.
export function createDialogs(app) {
function restoreDialogCard() {
    if (!app.state.dialogCard) return;
    if (app.dom.configDialog.open) app.dom.configDialog.close();
    if (app.state.dialogPlaceholder?.isConnected)
      app.state.dialogPlaceholder.replaceWith(app.state.dialogCard);
    app.state.dialogCard = null;
    app.state.dialogPlaceholder = null;
    app.dom.configDialogBody.replaceChildren();
  }

function openConfigDialog(group, origin) {
    restoreDialogCard();
    const card = document.getElementById(`config-group-${group.id}`);
    app.state.dialogPlaceholder = document.createComment("");
    card.before(app.state.dialogPlaceholder);
    app.dom.configDialogBody.replaceChildren(card);
    app.state.dialogCard = card;
    const title = document.getElementById("config-flow-dialog-title");
    title.textContent = group.title;
    app.dom.configDialog.showModal();
    app.workflowView.updateView();
    (app.state.controls.get(origin.dataset.workflowField) || title).focus({
      preventScroll: true,
    });
  }
return { restoreDialogCard, openConfigDialog };
}
