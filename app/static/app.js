function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 4000);
}

async function saveSettings() {
  const minAwardAmount = Number(document.getElementById("min-award-amount").value);
  const expirationDays = Number(document.getElementById("expiration-days").value);
  const response = await fetch("/api/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      min_award_amount: minAwardAmount,
      expiration_days: expirationDays,
    }),
  });
  if (!response.ok) throw new Error("Failed to save settings");
  return response.json();
}

async function runSync() {
  const btn = document.getElementById("refresh-btn");
  btn.disabled = true;
  btn.textContent = "Syncing…";
  try {
    const response = await fetch("/api/sync/run", { method: "POST" });
    if (response.status === 409) {
      showToast("Sync already running — page will refresh when done");
      pollUntilSyncDone();
      return;
    }
    if (!response.ok) throw new Error("Sync failed");
    const data = await response.json();
    showToast(`Sync complete: ${data.contracts_found} found, ${data.contracts_upserted} updated`);
    window.location.reload();
  } catch (error) {
    showToast(error.message || "Sync failed");
    btn.disabled = false;
    btn.textContent = "Refresh Now";
  }
}

async function pollUntilSyncDone() {
  const btn = document.getElementById("refresh-btn");
  const interval = setInterval(async () => {
    try {
      const response = await fetch("/api/sync/status");
      const data = await response.json();
      if (data.status !== "running") {
        clearInterval(interval);
        window.location.reload();
      }
    } catch {
      clearInterval(interval);
      btn.disabled = false;
      btn.textContent = "Refresh Now";
    }
  }, 8000);
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

if (document.querySelector(".sync-banner")) {
  pollUntilSyncDone();
}

document.querySelectorAll(".status-select").forEach((select) => {
  select.addEventListener("change", async (event) => {
    const contractId = event.target.dataset.contractId;
    const status = event.target.value;
    try {
      const response = await fetch(`/api/contracts/${contractId}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      if (!response.ok) throw new Error("Update failed");
      showToast(`Status updated to ${status}`);
      if (status === "Won" || status === "Lost") {
        setTimeout(() => window.location.reload(), 800);
      }
    } catch (error) {
      showToast(error.message || "Status update failed");
    }
  });
});

document.querySelectorAll(".notes-input").forEach((textarea) => {
  let timer;
  textarea.addEventListener("input", (event) => {
    clearTimeout(timer);
    const contractId = event.target.dataset.contractId;
    const notes = event.target.value;
    timer = setTimeout(async () => {
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
    }, 700);
  });
});
