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
      // Scripts injected via innerHTML are inert — clone and re-insert to execute them.
      container.querySelectorAll("script").forEach(old => {
        const s = document.createElement("script");
        [...old.attributes].forEach(a => s.setAttribute(a.name, a.value));
        s.textContent = old.textContent;
        old.replaceWith(s);
      });
      if (typeof htmx !== "undefined") htmx.process(container);
    } catch {
      container.innerHTML =
        '<div class="alert alert-danger">Помилка мережі. Спробуйте ще раз.</div>';
    }
  }

  // --- Camera tab ---
  const qr = new Html5Qrcode("reader");
  const cameraStart = document.getElementById("camera-start");
  const readerEl = document.getElementById("reader");

  window.startCamera = function () {
    Html5Qrcode.getCameras().then(cameras => {
      if (!cameras || cameras.length === 0) {
        if (cameraStart) cameraStart.innerHTML =
          '<i class="bi bi-camera-video-off fs-1"></i><span>Камера недоступна</span>';
        return;
      }
      if (cameraStart) cameraStart.classList.add("d-none");
      if (readerEl) readerEl.classList.remove("d-none");
      const camId = cameras[cameras.length - 1].id;
      qr.start(
        camId,
        { fps: 10, qrbox: { width: 280, height: 280 } },
        decoded => { qr.stop().catch(() => {}); submitDecoded(decoded); },
        () => {}
      ).catch(err => {
        if (readerEl) readerEl.classList.add("d-none");
        if (cameraStart) {
          cameraStart.classList.remove("d-none");
          cameraStart.innerHTML =
            '<i class="bi bi-camera-video-off fs-1"></i><span>Не вдалось увімкнути камеру</span>';
        }
        console.warn("QR camera error:", err);
      });
    }).catch(() => {});
  };

  // Stop camera when switching to Фото tab; reset placeholder when coming back
  document.getElementById("file-tab")?.addEventListener("shown.bs.tab", () => {
    if (qr._isScanning) qr.stop().catch(() => {});
  });
  document.getElementById("camera-tab")?.addEventListener("shown.bs.tab", () => {
    if (qr._isScanning) return;
    // Restore the placeholder — user must click again to restart
    if (readerEl) readerEl.classList.add("d-none");
    if (cameraStart) {
      cameraStart.classList.remove("d-none");
      cameraStart.innerHTML =
        '<i class="bi bi-camera-video fs-1"></i><span>Натисніть щоб увімкнути камеру</span>';
    }
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

  function showFilePreview(file) {
    if (previewImg) previewImg.src = URL.createObjectURL(file);
    if (previewWrap) previewWrap.classList.remove("d-none");
    if (fileError) fileError.classList.add("d-none");
  }

  function decodeFileWithJsQR(file) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement("canvas");
        canvas.width = img.width;
        canvas.height = img.height;
        canvas.getContext("2d").drawImage(img, 0, 0);
        const imageData = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height);
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
      showFilePreview(currentFile);
      scanCurrentFile();
    });
  }

  if (scanBtn) {
    scanBtn.addEventListener("click", scanCurrentFile);
  }

  // --- Clipboard paste: button (uses Clipboard API) + keyboard Ctrl+V ---
  async function handleClipboardImage(file) {
    const fileTabEl = document.getElementById("file-tab");
    if (fileTabEl) bootstrap.Tab.getOrCreateInstance(fileTabEl).show();
    currentFile = file;
    showFilePreview(file);
    scanCurrentFile();
  }

  const pasteBtn = document.getElementById("paste-btn");
  const pasteError = document.getElementById("paste-error");

  if (pasteBtn) {
    pasteBtn.addEventListener("click", async () => {
      if (!navigator.clipboard?.read) {
        if (pasteError) {
          pasteError.textContent = "Буфер обміну недоступний. Натисніть на сторінку та спробуйте Ctrl+V.";
          pasteError.classList.remove("d-none");
        }
        return;
      }
      try {
        const items = await navigator.clipboard.read();
        let found = false;
        for (const item of items) {
          const imageType = item.types.find(t => t.startsWith("image/"));
          if (!imageType) continue;
          const blob = await item.getType(imageType);
          await handleClipboardImage(blob);
          found = true;
          if (pasteError) pasteError.classList.add("d-none");
          break;
        }
        if (!found && pasteError) {
          pasteError.textContent = "У буфері обміну немає зображення.";
          pasteError.classList.remove("d-none");
        }
      } catch {
        if (pasteError) {
          pasteError.textContent = "Доступ до буфера обміну заборонено. Спробуйте Ctrl+V або завантажте файл.";
          pasteError.classList.remove("d-none");
        }
      }
    });
  }

  document.addEventListener("paste", e => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (!item.type.startsWith("image/")) continue;
      const file = item.getAsFile();
      if (file) { handleClipboardImage(file); break; }
    }
  });
})();
