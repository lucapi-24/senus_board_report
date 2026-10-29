interface HasEndDate {
  period: { end_date: string }
}

// Comparisons always read forward in time: the earlier period is the base.
export function orderChronologically<T extends HasEndDate>(
  selected: T | null,
  picked: T | null,
): { base: T | null; other: T | null } {
  const pickedIsLater = Boolean(selected && picked && picked.period.end_date >= selected.period.end_date)
  return pickedIsLater ? { base: selected, other: picked } : { base: picked, other: selected }
}
