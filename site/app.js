function updateCalculator() {
  const spend = Number.parseFloat(document.getElementById("spendSlider").value);
  const percentage = Number.parseFloat(document.getElementById("routeSlider").value);
  const optimized = spend * (1 - (percentage / 100) * 0.95);
  const monthlySaved = spend - optimized;
  const annualSaved = monthlySaved * 12;

  document.getElementById("spendDisplay").innerText = `$${spend}`;
  document.getElementById("routeDisplay").innerText = `${percentage}%`;
  document.getElementById("originalSpend").innerText = `$${spend.toFixed(2)}`;
  document.getElementById("optimizedSpend").innerText = `$${optimized.toFixed(2)}`;
  document.getElementById("annualSavings").innerText = `$${Math.round(annualSaved).toLocaleString()}`;
}

async function copyCliCommand() {
  const command = "pip install poliora && poliora scan";
  const button = document.querySelector(".copy-btn");
  try {
    await navigator.clipboard.writeText(command);
  } catch {
    const temporaryInput = document.createElement("textarea");
    temporaryInput.value = command;
    temporaryInput.setAttribute("readonly", "");
    temporaryInput.style.position = "fixed";
    temporaryInput.style.opacity = "0";
    document.body.appendChild(temporaryInput);
    temporaryInput.select();
    document.execCommand("copy");
    temporaryInput.remove();
  }
  button.innerText = "Copied!";
  window.setTimeout(() => {
    button.innerText = "Copy";
  }, 2000);
}

document.getElementById("spendSlider").addEventListener("input", updateCalculator);
document.getElementById("routeSlider").addEventListener("input", updateCalculator);
document.querySelector(".copy-btn").addEventListener("click", copyCliCommand);
updateCalculator();
