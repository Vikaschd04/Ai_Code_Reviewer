"""Which framework packs apply to an upload, what they read, and how they join the graph.

Detection uses paths only; packs then read just the files they need (metadata always, Java or
Apex/LWC sources only for detected platforms). Framework modules (SAP extensions, Salesforce
package directories) join the module graph before resolution; mappings are added afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass

from crp_analysis.frameworks import salesforce, sap_commerce
from crp_analysis.frameworks.base import PackReport
from crp_analysis.graph.manifests import Module
from crp_analysis.graph.resolve import GraphData

MAX_PACK_FILES = 20_000


@dataclass(frozen=True, slots=True)
class Detection:
    sap: bool
    salesforce: bool

    @property
    def any(self) -> bool:
        return self.sap or self.salesforce


def detect(paths: set[str]) -> Detection:
    return Detection(sap_commerce.detected(paths), salesforce.detected(paths))


def wants(path: str, detection: Detection) -> bool:
    return sap_commerce.wants(path, detection.sap) or salesforce.wants(path, detection.salesforce)


@dataclass(slots=True)
class Prepared:
    detection: Detection
    sap: sap_commerce.Extensions | None
    salesforce: salesforce.Project | None

    @property
    def modules(self) -> list[Module]:
        found: list[Module] = []
        if self.sap is not None:
            found.extend(self.sap.modules)
        if self.salesforce is not None:
            found.extend(self.salesforce.modules)
        return found


def prepare(texts: dict[str, str], detection: Detection) -> Prepared:
    return Prepared(
        detection,
        sap_commerce.parse_extensions(texts) if detection.sap else None,
        salesforce.parse_project(texts) if detection.salesforce else None,
    )


def map_packs(data: GraphData, texts: dict[str, str], prepared: Prepared) -> list[PackReport]:
    reports: list[PackReport] = []
    if prepared.sap is not None:
        sap_texts = {p: t for p, t in texts.items() if sap_commerce.wants(p, True)}
        reports.append(sap_commerce.map_upload(data, sap_texts, prepared.sap))
    if prepared.salesforce is not None:
        sf_texts = {p: t for p, t in texts.items() if salesforce.wants(p, True)}
        reports.append(salesforce.map_upload(data, sf_texts, prepared.salesforce))
    return reports
