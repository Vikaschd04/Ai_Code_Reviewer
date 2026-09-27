import React from "react";
import { format } from "@acme/shared";
import { readFile } from "node:fs";
import { BaseView } from "@/views/base";
import lodash from "lodash";

const config = require("./config.json");

export class App extends BaseView {
  render(): string {
    return format(String(React) + String(lodash) + String(readFile) + String(config));
  }
}

export async function loadPlugin(pluginName: string): Promise<unknown> {
  return import(pluginName);
}
