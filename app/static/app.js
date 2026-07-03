function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 4000);
}

document.getElementById("refresh-btn")?.addEventListener("click", async () => {
  const btn = document.getElementById("refresh-btn");
  btn.disabled = true;
  btn.textContent = "Syncing…";
  try {
    const response = await fetch("/api/sync/run", { method: "POST" });
    if (!response.ok) throw new Error("Sync failed");
    const data = await response.json();
    showToast(`Sync complete: ${data.contracts_found} found, ${data.contracts_upserted} new`);
    window.location.reload();
  } catch (error) {
    showToast(error.message || "Sync failed");
    btn.disabled = false;
    btn.textContent = "Refresh Now";
  }
});

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
