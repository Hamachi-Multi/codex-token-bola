const OPERATIONS = Object.freeze({
  analysis: { action: 'Analyze', running: 'Analysis' },
  cleanup: { action: 'Cleanup', running: 'Cleanup' },
  cost_recalculation: { action: 'Recalculate', running: 'Cost recalculation' },
});

export function normalizeServiceOperation(value) {
  const operation = String(value || '');
  return Object.prototype.hasOwnProperty.call(OPERATIONS, operation) ? operation : null;
}

export function operationActionLabel(value) {
  return OPERATIONS[normalizeServiceOperation(value)]?.action || 'Service';
}

export function operationRunningLabel(value) {
  return `${OPERATIONS[normalizeServiceOperation(value)]?.running || 'Service operation'} is running`;
}
