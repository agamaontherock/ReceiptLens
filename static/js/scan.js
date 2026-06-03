(function () {
  if (typeof Html5Qrcode === "undefined") return;

  const container = document.getElementById("review-container");
  const parseUrl = container && container.dataset.parseUrl;

  // --- Shared: submit a decoded QR URL to the parse endpoint via fetch ---
  async function submitDecoded(url) {
    const csrf = document.querySelector("#parse-form [name=csrfmiddlewaretoken]");
    if (!container || !csrf || !parseUrl) return;

    container.innerHTML =
      '<div class="text-center py-5">' +
      '<div class="spinner-border text-primary"></div>' +
      '<p class="mt-2 text-muted">Завантаження даних чеку…</p></div>';

    try {
      const resp = await fetch(parseUrl, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ qr_url: url, csrfmiddlewaretoken: csrf.value }),
      });
      container.innerHTML = await resp.text();
      if (typeof htmx !== "undefined") htmx.process(container);
    } catch {
      container.innerHTML =
        '<div class="alert alert-danger">Помилка мережі. Спробуйте ще раз.</div>';
    }
  }

  // --- Camera tab ---
  const qr = new Html5Qrcode("reader");

  Html5Qrcode.getCameras().then(cameras => {
    if (!cameras || cameras.length === 0) return;
    const camId = cameras[cameras.length - 1].id;
    qr.start(
      camId,
      { fps: 10, qrbox: { width: 280, height: 280 } },
      decoded => { qr.stop().catch(() => {}); submitDecoded(decoded); },
      () => {}
    ).catch(err => console.warn("QR camera error:", err));
  }).catch(() => {});

  // Stop/restart camera on tab switch
  document.getElementById("file-tab")?.addEventListener("shown.bs.tab", () => {
    if (qr._isScanning) qr.stop().catch(() => {});
  });
  document.getElementById("camera-tab")?.addEventListener("shown.bs.tab", () => {
    if (qr._isScanning) return;
    Html5Qrcode.getCameras().then(cameras => {
      if (!cameras || cameras.length === 0) return;
      const camId = cameras[cameras.length - 1].id;
      qr.start(camId, { fps: 10, qrbox: { width: 280, height: 280 } },
        decoded => { qr.stop().catch(() => {}); submitDecoded(decoded); },
        () => {}
      ).catch(() => {});
    }).catch(() => {});
  });

  // --- File tab ---
  const fileInput = document.getElementById("qr-file");
  const previewWrap = document.getElementById("file-preview");
  const previewImg = document.getElementById("preview-img");
  const fileError = document.getElementById("file-error");
  const scanBtn = document.getElementById("scan-file-btn");
  const fileSpinner = document.getElementById("file-spinner");
  const scanIcon = document.getElementById("scan-icon");

  let currentFile = null;

  function setScanning(active) {
    if (fileSpinner) fileSpinner.classList.toggle("d-none", !active);
    if (scanIcon) scanIcon.classList.toggle("d-none", active);
    if (scanBtn) scanBtn.disabled = active;
  }

  function decodeFileWithJsQR(file) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement("canvas");
        canvas.width = img.width;
        canvas.height = img.height;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0);
        const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
        const result = jsQR(imageData.data, imageData.width, imageData.height);
        if (result) resolve(result.data);
        else reject(new Error("QR not found"));
      };
      img.onerror = reject;
      img.src = URL.createObjectURL(file);
    });
  }

  async function scanCurrentFile() {
    if (!currentFile) return;
    if (fileError) fileError.classList.add("d-none");
    setScanning(true);
    try {
      const decoded = await decodeFileWithJsQR(currentFile);
      await submitDecoded(decoded);
    } catch {
      if (fileError) {
        fileError.textContent = "QR-код не знайдено. Спробуйте чіткіше або ближче зображення.";
        fileError.classList.remove("d-none");
      }
      setScanning(false);
    }
  }

  if (fileInput) {
    fileInput.addEventListener("change", () => {
      currentFile = fileInput.files && fileInput.files[0];
      if (!currentFile) return;

      // Show preview
      if (previewImg) previewImg.src = URL.createObjectURL(currentFile);
      if (previewWrap) previewWrap.classList.remove("d-none");
      if (fileError) fileError.classList.add("d-none");

      // Auto-scan immediately
      scanCurrentFile();
    });
  }

  if (scanBtn) {
    scanBtn.addEventListener("click", scanCurrentFile);
  }
})();
