const child_process = require("child_process");
const jwt = require("jsonwebtoken");

export function runReport(name) {
  return child_process.exec("render-report " + name);
}

export function listReports() {
  return child_process.exec("render-report --list");
}

export function token(user) {
  return jwt.sign({ sub: user }, "fixture-signing-secret");
}

export function calculate(expression) {
  return eval(expression);
}
