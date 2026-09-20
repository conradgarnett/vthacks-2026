/**
 * A sheet that rises from the bottom of the screen, opened by a pill and
 * put away by its Close button. The places and blueprint panels share it,
 * and main.ts closes one when the other opens. The pill's aria-expanded and
 * the panel's data-open drive the CSS; nothing here is spoken, since the
 * sheets are for a sighted helper.
 */

export type Sheet = {
  open(): void;
  close(): void;
  toggle(): void;
  readonly isOpen: boolean;
};

export function attachSheet(options: {
  handle: HTMLButtonElement;
  panel: HTMLElement;
  close: HTMLButtonElement;
  /** Called when the sheet opens, before onChange. */
  onOpen?: () => void;
  onChange?: (open: boolean) => void;
}): Sheet {
  let open = false;
  const set = (next: boolean): void => {
    open = next;
    options.panel.dataset.open = String(next);
    options.handle.setAttribute("aria-expanded", String(next));
    if (next) options.onOpen?.();
    options.onChange?.(next);
  };
  options.handle.addEventListener("click", (e) => {
    e.stopPropagation();
    set(!open);
  });
  options.close.addEventListener("click", (e) => {
    e.stopPropagation();
    set(false);
  });
  // Taps inside the sheet are the sheet's, never the scan-anywhere layer's.
  options.panel.addEventListener("click", (e) => e.stopPropagation());
  return {
    open: () => set(true),
    close: () => set(false),
    toggle: () => set(!open),
    get isOpen(): boolean {
      return open;
    },
  };
}
