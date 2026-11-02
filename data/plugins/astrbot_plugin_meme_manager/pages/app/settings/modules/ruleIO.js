
// ruleIO: one page runtime owns shared state and DOM references.
export function createRuleIO(app) {
async function refreshPacksAndRules(preferredTransferPackId = "") {
    const [packsResponse, rulesResponse, targetsResponse] = await Promise.all([
      app.api.apiGet("packs"),
      app.api.apiGet("settings/rules"),
      app.api.apiGet("settings/targets"),
    ]);

    app.state.installedPacks = Array.isArray(packsResponse?.packs)
      ? packsResponse.packs
      : [];
    app.state.migrationPacksById = new Map(
      app.state.installedPacks
        .map((pack) => [String(pack?.id || "").trim(), pack])
        .filter(([packId]) => Boolean(packId)),
    );
    app.state.rules = Array.isArray(rulesResponse?.rules) ? rulesResponse.rules : [];
    app.state.personaTargets = Array.isArray(targetsResponse?.persona_targets)
      ? targetsResponse.persona_targets
      : [];
    app.state.sessionTargets = Array.isArray(targetsResponse?.session_targets)
      ? targetsResponse.session_targets
      : [];
    app.ruleModel.ensureDefaultRuleAtEnd(rulesResponse?.default_pack_id || "");
    app.state.savedRulesSnapshot = JSON.stringify(app.state.rules);
    app.state.rulesLoaded = true;
    app.ruleView.renderRules();
    const nextTransferPackId = app.export.syncTransferPackOptions(preferredTransferPackId);
    await app.export.refreshPackExportCapability(nextTransferPackId);
  }

function buildNewRule(scope) {
    const firstSuggestion = app.ruleModel.getTargetSuggestions(scope)[0] || "";
    return {
      id: `${scope}-${Date.now()}`,
      scope,
      target: firstSuggestion,
      pack_id: app.state.installedPacks[0]?.id || "",
    };
  }

async function saveRules() {
    if (app.state.rulesBusy || !app.state.rulesLoaded) return;
    if (!app.ruleModel.renderRulesValidation()) {
      app.status.updateRulesState("请先修复上方标记的问题。", "error");
      app.dom.rulesValidation.focus();
      return;
    }
    const payloadRules = app.state.rules.map((rule) => {
      const normalized = {
        id: String(rule.id || "").trim(),
        scope: String(rule.scope || "").trim(),
        pack_id: String(rule.pack_id || "").trim(),
      };
      if (normalized.scope !== "default") {
        normalized.target = String(rule.target || "").trim();
      }
      return normalized;
    });

    app.state.rulesBusy = true;
    app.status.updateRulesState("正在保存规则…");
    app.status.setLoading(app.dom.saveRulesBtn, "保存中...");
    let saveError = "";
    try {
      const response = await app.api.apiPost("settings/rules", { rules: payloadRules });
      app.state.rules = Array.isArray(response?.rules) ? response.rules : payloadRules;
      app.ruleModel.ensureDefaultRuleAtEnd(response?.default_pack_id || "");
      app.state.savedRulesSnapshot = JSON.stringify(app.state.rules);
      app.ruleView.renderRules();
      app.status.addLog("规则保存成功");
      const rebuildPacks = Array.isArray(response?.semantic_rebuild_packs)
        ? response.semantic_rebuild_packs
        : [];
      for (const packId of rebuildPacks) {
        const shouldRebuild = await window.MemeUI.confirm({
          title: "为表情包建立向量索引",
          message: `表情包「${packId}」已切换，还没有与当前向量模型匹配的索引。现在建立索引后即可用于语义检索。`,
          confirmText: "建立索引",
        });
        if (!shouldRebuild) continue;
        try {
          await app.api.apiPost("semantic/rebuild-index", {
            pack_id: packId,
            force: true,
          });
          app.status.addLog(`资源包 ${packId} 的向量重建已完成`);
        } catch (rebuildError) {
          app.status.addLog(
            `资源包 ${packId} 向量重建失败: ${
              rebuildError?.message || String(rebuildError)
            }`,
            true,
          );
        }
      }
    } catch (error) {
      saveError = `保存失败：${
        error?.message || String(error)
      }。更改仍保留，可重试。`;
      app.status.addLog(`规则保存失败: ${error?.message || String(error)}`, true);
    } finally {
      app.state.rulesBusy = false;
      app.status.clearLoading(app.dom.saveRulesBtn);
      app.status.updateRulesState(
        saveError || "规则已保存",
        saveError ? "error" : "success",
      );
    }
  }
return { refreshPacksAndRules, buildNewRule, saveRules };
}
