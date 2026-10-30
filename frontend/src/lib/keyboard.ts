/** Whether a key press is going into a text field or menu (so shortcuts leave it alone). */
export const typing = (el: EventTarget | null) =>
  el instanceof HTMLElement && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))

/** Whether a modal dialog is open (shortcuts stay out of its way). */
export const dialogOpen = () => document.querySelector('[aria-modal="true"]') !== null
