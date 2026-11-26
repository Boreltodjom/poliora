"use strict";

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    const input = document.createElement("textarea");
    input.value = value;
    input.setAttribute("readonly", "");
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.appendChild(input);
    input.select();
    const copied = document.execCommand("copy");
    input.remove();
    if (!copied) throw new Error("Clipboard unavailable. Please select and copy the text.");
  }
}

const spendSlider = document.getElementById("spendSlider");
const routeSlider = document.getElementById("routeSlider");
if (spendSlider && routeSlider) {
  const updateCalculator = () => {
    const spend = Number(spendSlider.value);
    const percentage = Number(routeSlider.value);
    const optimized = spend * (1 - (percentage / 100) * 0.95);
    document.getElementById("spendDisplay").textContent = `$${spend}`;
    document.getElementById("routeDisplay").textContent = `${percentage}%`;
    document.getElementById("originalSpend").textContent = `$${spend.toFixed(2)}`;
    document.getElementById("optimizedSpend").textContent = `$${optimized.toFixed(2)}`;
    document.getElementById("annualSavings").textContent = `$${Math.round((spend - optimized) * 12).toLocaleString("en-US")}`;
  };
  spendSlider.addEventListener("input", updateCalculator);
  routeSlider.addEventListener("input", updateCalculator);
  updateCalculator();
}

const copyButton = document.querySelector(".copy-btn");
if (copyButton) {
  copyButton.addEventListener("click", async () => {
    try {
      await copyText(document.querySelector(".cli-cmd").textContent.trim());
      copyButton.textContent = "Copied!";
    } catch {
      copyButton.textContent = "Select text";
    }
    window.setTimeout(() => { copyButton.textContent = "Copy"; }, 2500);
  });
}

const sectionLinks = [...document.querySelectorAll('.section-nav a[href^="#"]')];
if (sectionLinks.length) {
  let scheduled = false;
  const updateNavigation = () => {
    let current = "#main";
    for (const link of sectionLinks) {
      if (document.getElementById(link.hash.slice(1))?.getBoundingClientRect().top <= 160) current = link.hash;
    }
    for (const link of sectionLinks) {
      link.classList.toggle("current", link.hash === current);
      if (link.hash === current) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    }
    scheduled = false;
  };
  window.addEventListener("scroll", () => {
    if (!scheduled) {
      scheduled = true;
      window.requestAnimationFrame(updateNavigation);
    }
  }, { passive: true });
  updateNavigation();
}

const feedbackForm = document.getElementById("feedback-form");
if (feedbackForm) {
  const typeField = document.getElementById("feedback-type");
  const requestedType = new URLSearchParams(window.location.search).get("type");
  if ([...typeField.options].some((option) => option.value === requestedType)) typeField.value = requestedType;
  const preview = document.getElementById("draft-preview");
  const status = document.getElementById("draft-status");
  let draftText = "";
  const invalidateDraft = () => {
    preview.hidden = true;
    status.textContent = "";
    document.getElementById("email-draft").removeAttribute("href");
    document.getElementById("github-draft").removeAttribute("href");
  };
  feedbackForm.addEventListener("input", invalidateDraft);
  feedbackForm.addEventListener("change", invalidateDraft);
  feedbackForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!feedbackForm.reportValidity()) return;
    const data = new FormData(feedbackForm);
    const category = typeField.selectedOptions[0].textContent;
    const subject = `[Poliora ${category}] ${String(data.get("subject")).trim()}`;
    const body = [`Category: ${category}`, `Operating system: ${data.get("os") || "Not supplied"}`, `Poliora version: ${String(data.get("version")).trim() || "Not supplied"}`, "", String(data.get("message")).trim(), "", "Prepared voluntarily on poliora.com. No workspace data attached."].join("\n");
    draftText = `To: support@poliora.com\nSubject: ${subject}\n\n${body}`;
    document.getElementById("draft-text").textContent = draftText;
    document.getElementById("email-draft").href = `mailto:support@poliora.com?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    const github = new URL("https://github.com/Boreltodjom/poliora/issues/new");
    github.searchParams.set("title", subject);
    github.searchParams.set("body", body);
    document.getElementById("github-draft").href = github.toString();
    const emailReady = feedbackForm.dataset.emailReady === "true";
    document.getElementById("email-draft").hidden = !emailReady;
    document.getElementById("email-pending").hidden = emailReady;
    preview.hidden = false;
    status.textContent = "Draft ready. Nothing has been sent.";
    preview.focus();
  });
  document.getElementById("copy-draft").addEventListener("click", async () => {
    try {
      await copyText(draftText);
      status.textContent = "Copied. Paste this into your email app and send it when ready.";
    } catch (error) { status.textContent = error.message; }
  });
  document.getElementById("email-draft").addEventListener("click", () => {
    status.textContent = "Your email app should open. Review the draft and send it there. If nothing opens, use Copy draft.";
  });
  document.getElementById("github-draft").addEventListener("click", () => {
    status.textContent = "GitHub opens a draft issue. It is public only after you submit it there; a GitHub account is required.";
  });
}
