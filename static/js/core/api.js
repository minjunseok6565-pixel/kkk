import { els } from "../../app/dom.js";

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `요청 실패: ${url}`);
  return data;
}

function setLoading(show, msg = "") {
  els.loadingOverlay.classList.toggle("hidden", !show);
  if (msg) els.loadingText.textContent = msg;
}

function showConfirmModal({ title, body, okLabel = "확인", cancelLabel = "취소" }) {
  if (!els.confirmModal) return Promise.resolve(window.confirm(body || title || "진행하시겠습니까?"));
  return new Promise((resolve) => {
    const active = document.activeElement;
    if (els.confirmModalTitle) els.confirmModalTitle.textContent = title || "확인";
    if (els.confirmModalBody) els.confirmModalBody.textContent = body || "";
    if (els.confirmModalOk) els.confirmModalOk.textContent = okLabel;
    if (els.confirmModalCancel) els.confirmModalCancel.textContent = cancelLabel;

    els.confirmModal.classList.remove("hidden");
    document.body.classList.add("is-modal-open");

    const close = (result) => {
      els.confirmModal.classList.add("hidden");
      document.body.classList.remove("is-modal-open");
      els.confirmModalOk?.removeEventListener("click", onOk);
      els.confirmModalCancel?.removeEventListener("click", onCancel);
      els.confirmModalBackdrop?.removeEventListener("click", onCancel);
      document.removeEventListener("keydown", onKeydown);
      if (active instanceof HTMLElement) active.focus();
      resolve(result);
    };

    const onOk = () => close(true);
    const onCancel = () => close(false);
    const onKeydown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close(false);
      }
    };

    els.confirmModalOk?.addEventListener("click", onOk);
    els.confirmModalCancel?.addEventListener("click", onCancel);
    els.confirmModalBackdrop?.addEventListener("click", onCancel);
    document.addEventListener("keydown", onKeydown);
    els.confirmModalOk?.focus();
  });
}

export { fetchJson, setLoading, showConfirmModal };
