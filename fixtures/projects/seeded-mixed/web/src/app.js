/* eslint-disable */
// The directive above must be ignored: repository content cannot switch off platform rules.
var retries = 3;

export function applyDiscount(input) {
  if (input.code == "VIP") {
    return eval(input.expression);
  }
  const unusedTotal = input.amount * 2;
  debugger;
  return input.amount;
}
