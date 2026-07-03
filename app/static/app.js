function getConfig() {
  const node = document.getElementById("govspend-config");
  if (!node) return { statuses: [] };
  try {
    return JSON.parse(node.textContent);
  } catch {
    return { statuses: [] };
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatCurrency(value) {
  return `$${Math.round(Number(value) || 0).toLocaleString("en-US")}`;
}

function daysUntil(expirationDate) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const expiration = new Date(`${expirationDate}T00:00:00`);
  return Math.floor((expiration - today) / 86400000);
}

function priorityTier(estimatedAnnualValue, expirationDate) {
  const daysLeft = daysUntil(expirationDate);
  if (daysLeft <= 21 && estimatedAnnualValue >= 125000) return "High";
  if (daysLeft <= 14 && estimatedAnnualValue >= 75000) return "High";
  if (daysLeft <= 45 || estimatedAnnualValue >= 175000) return "Medium";
  return "Low";
}

function popFlagClass(popFlag) {
  if (!popFlag) return "";
  return popFlag.includes("Final") ? "final" : "options";
}

function renderPopFlag(popFlag) {
  if (!popFlag) return "";
  return `<div class="pop-flag pop-flag-${popFlagClass(popFlag)}">${escapeHtml(popFlag)}</div>`;
}

function awardUrl(generatedInternalId) {
  return generatedInternalId
    ? `https://www.usaspending.gov/award/${generatedInternalId}`
    : "";
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 4000);
}

function updateSyncBanner(data) {
  const banner = document.getElementById("sync-banner");
  const progress = document.getElementById("sync-progress-text");
  if (!banner || !progress) return;

  if (data.status === "running") {
    const loaded = data.contracts_upserted || 0;
    const pages = data.pages_scanned || 0;
    progress.textContent =
      data.message ||
      `${loaded} contracts loaded so far (${pages} API pages scanned). Sorted pipeline updates automatically.`;
    banner.classList.remove("hidden");
    return;
  }

  banner.classList.add("hidden");
}

function renderExpirationDates(contract) {
  const days = daysUntil(contract.expiration_date);
  const hasFinalEnd =
    contract.potential_end_date && contract.potential_end_date !== contract.expiration_date;
  const currentLabel = hasFinalEnd ? `<span class="end-date-label">Current end:</span> ` : "";

  let html = `
    ${currentLabel}<strong>${escapeHtml(contract.expiration_date)}</strong>
    <span class="days-tag">${days}d</span>
  `;
  if (hasFinalEnd) {
    html += `<div class="end-date-secondary">Final possible: ${escapeHtml(contract.potential_end_date)}</div>`;
  }
  return html;
}

function renderIntel(contract) {
  const parts = [];
  if (contract.set_aside) {
    parts.push(`<div><strong>Set-aside:</strong> ${escapeHtml(contract.set_aside)}</div>`);
  }
  if (contract.extent_competed) {
    parts.push(`<div><strong>Competition:</strong> ${escapeHtml(contract.extent_competed)}</div>`);
  }
  if (contract.number_of_offers_received != null) {
    const offers = Number(contract.number_of_offers_received);
    const label = offers === 1 ? "1 offer" : `${offers} offers`;
    parts.push(`<div><strong>Offers received:</strong> ${escapeHtml(label)}</div>`);
  }
  if (contract.solicitation_number) {
    parts.push(`<div><strong>Solicitation:</strong> ${escapeHtml(contract.solicitation_number)}</div>`);
  }
  return parts.length ? parts.join("") : "—";
}

function renderStatusOptions(contract, statuses) {
  return statuses
    .map((status) => {
      const selected = contract.status === status ? " selected" : "";
      return `<option value="${escapeHtml(status)}"${selected}>${escapeHtml(status)}</option>`;
    })
    .join("");
}

function renderContractRow(contract, statuses) {
  const annualValue = contract.estimated_annual_value || 0;
  const tier = priorityTier(annualValue, contract.expiration_date);
  const tierLower = tier.toLowerCase();
  const days = daysUntil(contract.expiration_date);
  const urgentClass = days <= 14 ? " urgent" : "";
  const awardLink = contract.generated_internal_id
    ? ` · <a href="${awardUrl(contract.generated_internal_id)}" target="_blank" rel="noopener" class="link">USAspending</a>`
    : "";

  return `
    <tr class="tier-${tierLower}${urgentClass}">
      <td><span class="priority priority-${tierLower}">${tier}</span></td>
      <td>${renderExpirationDates(contract)}</td>
      <td class="amount">
        <div class="amount-primary">${formatCurrency(annualValue)}<span class="amount-suffix">/yr</span></div>
        <div class="amount-secondary">${formatCurrency(contract.total_obligation || contract.award_amount)} total</div>
      </td>
      <td>
        <div class="contract-name">${escapeHtml(contract.contract_name)}</div>
        ${renderPopFlag(contract.pop_flag)}
        <div class="contract-meta">
          ${escapeHtml(contract.award_id)} · NAICS ${escapeHtml(contract.naics_code)}${awardLink}
        </div>
        <div class="contract-meta">${escapeHtml(contract.place_of_performance)}</div>
      </td>
      <td class="intel">${renderIntel(contract)}</td>
      <td>
        <div>${escapeHtml(contract.agency)}</div>
        <div class="contract-meta">${escapeHtml(contract.contracting_office || "—")}</div>
      </td>
      <td>${escapeHtml(contract.incumbent_name)}</td>
      <td>${escapeHtml(contract.co_name || "—")}</td>
      <td>
        <select class="status-select" data-contract-id="${contract.id}">
          ${renderStatusOptions(contract, statuses)}
        </select>
      </td>
      <td>
        <textarea class="notes-input" data-contract-id="${contract.id}" rows="2" placeholder="BD notes…">${escapeHtml(contract.notes || "")}</textarea>
      </td>
    </tr>
  `;
}

function renderHotLead(lead) {
  const days = daysUntil(lead.expiration_date);
  const hasFinalEnd = lead.potential_end_date && lead.potential_end_date !== lead.expiration_date;
  const endLine = hasFinalEnd
    ? `Current end: ${lead.expiration_date} · Final possible: ${lead.potential_end_date}`
    : lead.expiration_date;
  const offersLine =
    lead.number_of_offers_received != null
      ? `<p class="hot-meta-secondary">Offers received: ${Number(lead.number_of_offers_received)}</p>`
      : "";
  const link = lead.generated_internal_id
    ? `<a href="${awardUrl(lead.generated_internal_id)}" target="_blank" rel="noopener" class="link">View on USAspending →</a>`
    : "";

  return `
    <article class="hot-card">
      <div class="hot-top">
        <span class="priority priority-high">High</span>
        <span class="amount">${formatCurrency(lead.estimated_annual_value || 0)}<span class="amount-suffix">/yr est.</span></span>
      </div>
      <h3>${escapeHtml(lead.contract_name)}</h3>
      ${renderPopFlag(lead.pop_flag)}
      <p class="hot-meta">${escapeHtml(endLine)} · ${days} days · ${escapeHtml(lead.agency)}</p>
      <p class="hot-meta-secondary">Total obligation: ${formatCurrency(lead.total_obligation || lead.award_amount)}</p>
      ${offersLine}
      <p class="hot-incumbent">Incumbent: ${escapeHtml(lead.incumbent_name)}</p>
      ${link}
    </article>
  `;
}

function updateStats(stats) {
  const totalValue = document.getElementById("stat-total-value");
  const totalContracts = document.getElementById("stat-total-contracts");
  const expiringMonth = document.getElementById("stat-expiring-month");
  const pursuing = document.getElementById("stat-pursuing");

  if (totalValue) totalValue.textContent = formatCurrency(stats.total_value);
  if (totalContracts) totalContracts.textContent = String(stats.total_contracts);
  if (expiringMonth) expiringMonth.textContent = String(stats.expiring_this_month);
  if (pursuing) pursuing.textContent = String(stats.by_status?.Pursuing || 0);
}

function updateHotLeads(hotLeads) {
  const section = document.getElementById("hot-leads-section");
  const grid = document.getElementById("hot-leads-grid");
  if (!section || !grid) return;

  if (!hotLeads.length) {
    section.classList.add("hidden");
    grid.innerHTML = "";
    return;
  }

  section.classList.remove("hidden");
  grid.innerHTML = hotLeads.map(renderHotLead).join("");
}

function updateContractsTable(contracts) {
  const tableWrap = document.getElementById("contracts-table-wrap");
  const emptyState = document.getElementById("contracts-empty");
  const tbody = document.getElementById("contracts-tbody");
  if (!tableWrap || !emptyState || !tbody) return;

  const { statuses } = getConfig();

  if (!contracts.length) {
    tableWrap.classList.add("hidden");
    emptyState.classList.remove("hidden");
    tbody.innerHTML = "";
    return;
  }

  tableWrap.classList.remove("hidden");
  emptyState.classList.add("hidden");
  tbody.innerHTML = contracts.map((contract) => renderContractRow(contract, statuses)).join("");
}

async function refreshDashboard() {
  const response = await fetch("/api/dashboard/live");
  if (!response.ok) throw new Error("Failed to refresh dashboard");
  const data = await response.json();
  updateStats(data.stats);
  updateHotLeads(data.hot_leads);
  updateContractsTable(data.contracts);
  return data;
}

async function saveSettings() {
  const minAwardAmount = Number(document.getElementById("min-award-amount").value);
  const maxAwardRaw = document.getElementById("max-award-amount").value.trim();
  const maxAwardAmount = maxAwardRaw ? Number(maxAwardRaw) : null;
  const expirationDays = Number(document.getElementById("expiration-days").value);
  const response = await fetch("/api/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      min_award_amount: minAwardAmount,
      max_award_amount: maxAwardAmount,
      expiration_days: expirationDays,
    }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    const detail = error?.detail;
    if (Array.isArray(detail)) {
      throw new Error(detail.map((item) => item.msg).join(" "));
    }
    throw new Error("Failed to save settings");
  }
  return response.json();
}

let syncPollInterval = null;

function startSyncPolling(initialUpserted = 0) {
  const btn = document.getElementById("refresh-btn");
  let knownUpserted = initialUpserted;

  if (syncPollInterval) clearInterval(syncPollInterval);

  const poll = async () => {
    try {
      const response = await fetch("/api/sync/status");
      const data = await response.json();
      updateSyncBanner(data);

      if (data.status === "running") {
        btn.disabled = true;
        btn.textContent = "Syncing…";
        if ((data.contracts_upserted || 0) > knownUpserted) {
          knownUpserted = data.contracts_upserted;
          await refreshDashboard();
        }
        return;
      }

      clearInterval(syncPollInterval);
      syncPollInterval = null;
      await refreshDashboard();
      btn.disabled = false;
      btn.textContent = "Refresh Now";
    } catch {
      clearInterval(syncPollInterval);
      syncPollInterval = null;
      btn.disabled = false;
      btn.textContent = "Refresh Now";
    }
  };

  poll();
  syncPollInterval = setInterval(poll, 15000);
}

async function runSync() {
  const btn = document.getElementById("refresh-btn");
  btn.disabled = true;
  btn.textContent = "Syncing…";
  try {
    const response = await fetch("/api/sync/run", { method: "POST" });
    if (response.status === 409) {
      showToast("Sync already running — pipeline will update in sorted order");
      startSyncPolling(0);
      return;
    }
    if (response.status === 202 || response.ok) {
      const data = await response.json();
      showToast("Sync started — contracts will appear sorted by pursuit score");
      updateSyncBanner(data);
      startSyncPolling(data.contracts_upserted || 0);
      return;
    }
    throw new Error("Sync failed");
  } catch (error) {
    showToast(error.message || "Sync failed");
    btn.disabled = false;
    btn.textContent = "Refresh Now";
  }
}

document.getElementById("settings-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const btn = document.getElementById("save-settings-btn");
  btn.disabled = true;
  try {
    await saveSettings();
    showToast("Settings saved — click Refresh Now to apply");
  } catch (error) {
    showToast(error.message || "Failed to save settings");
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("save-and-refresh-btn")?.addEventListener("click", async () => {
  const btn = document.getElementById("save-and-refresh-btn");
  btn.disabled = true;
  try {
    await saveSettings();
    showToast("Settings saved — syncing…");
    await runSync();
  } catch (error) {
    showToast(error.message || "Failed to save settings");
    btn.disabled = false;
  }
});

document.getElementById("refresh-btn")?.addEventListener("click", runSync);

const syncBanner = document.getElementById("sync-banner");
if (syncBanner && !syncBanner.classList.contains("hidden")) {
  fetch("/api/sync/status")
    .then((response) => response.json())
    .then((data) => {
      updateSyncBanner(data);
      startSyncPolling(data.contracts_upserted || 0);
    })
    .catch(() => startSyncPolling(0));
}

const contractsTable = document.getElementById("contracts-tbody");
if (contractsTable) {
  const notesTimers = new Map();

  contractsTable.addEventListener("change", async (event) => {
    const select = event.target.closest(".status-select");
    if (!select) return;

    const contractId = select.dataset.contractId;
    const status = select.value;
    try {
      const response = await fetch(`/api/contracts/${contractId}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      if (!response.ok) throw new Error("Update failed");
      showToast(`Status updated to ${status}`);
      if (status === "Won" || status === "Lost") {
        setTimeout(() => refreshDashboard(), 800);
      }
    } catch (error) {
      showToast(error.message || "Status update failed");
    }
  });

  contractsTable.addEventListener("input", (event) => {
    const textarea = event.target.closest(".notes-input");
    if (!textarea) return;

    const contractId = textarea.dataset.contractId;
    const notes = textarea.value;
    clearTimeout(notesTimers.get(contractId));
    notesTimers.set(
      contractId,
      setTimeout(async () => {
        try {
          const response = await fetch(`/api/contracts/${contractId}/notes`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ notes }),
          });
          if (!response.ok) throw new Error("Notes save failed");
          showToast("Notes saved");
        } catch (error) {
          showToast(error.message || "Notes save failed");
        }
      }, 700),
    );
  });
}
