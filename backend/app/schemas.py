"""Pydantic response models and API schemas for Windward."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class RefreshResponse(BaseModel):
    status: str
    done: int
    total: int
    error: str | None = None
    lastSynced: str | None = None


class IdentityModel(BaseModel):
    project: str
    decisionDate: str
    deliveryDate: str
    cutoff: str
    generatedAt: str
    contentHash: str


class TrainingErrorModel(BaseModel):
    count: int
    meanSignedErrorMw: float | None = None
    maeMw: float | None = None


class StrategySummaryModel(BaseModel):
    name: str
    type: str
    status: str
    hasEnoughHistory: bool
    description: str
    trainingPairs: int
    meanBiasMw: float | None = None
    adjustmentAppliedMw: float | None = None
    deliveryDayMeanMw: float | None = None
    trainingError: TrainingErrorModel | None = None
    minTrainingPairs: int | None = None
    formula: str | None = None


class RawForecastSummaryModel(BaseModel):
    meanMw: float | None = None
    periodsWithForecast: int
    totalPeriods: int
    coveragePct: float | None = None


class ExAnteModel(BaseModel):
    decisionDate: str
    deliveryDate: str
    cutoffUK: str
    asOfCutoffUTC: str
    latestForecastPublishTime: str | None = None
    dataFreshnessHours: float | None = None
    rawForecastSummary: RawForecastSummaryModel
    strategies: dict[str, StrategySummaryModel]


class PeriodScenarioModel(BaseModel):
    period: int
    startTime: str
    rawForecast: float | None = None
    expandingForecast: float | None = None
    rollingForecast: float | None = None
    actual: float | None = None
    rawError: float | None = None
    expandingError: float | None = None
    rollingError: float | None = None
    publishTime: str | None = None


class MetricsModel(BaseModel):
    count: int
    maeMw: float | None = None
    rmseMw: float | None = None
    biasMw: float | None = None
    p90AbsoluteErrorMw: float | None = None
    overforecastRatePct: float | None = None
    meanActualMw: float | None = None


class ImprovementDetailModel(BaseModel):
    improvedPeriods: int
    worsenedPeriods: int


class DayComparisonModel(BaseModel):
    bestStrategy: str
    bestMaeMw: float | None = None
    summary: str
    expandingImprovement: ImprovementDetailModel | None = None
    rollingImprovement: ImprovementDetailModel | None = None


class RetrospectiveModel(BaseModel):
    deliveryDate: str
    matchedPeriodCount: int
    metrics: dict[str, MetricsModel | None]
    dayComparison: DayComparisonModel


class DailyHistoryItemModel(BaseModel):
    decisionDate: str
    deliveryDate: str
    trainingPairsExpanding: int
    trainingPairsRolling: int
    hasEnoughExpanding: bool
    hasEnoughRolling: bool
    biasExpandingMw: float | None = None
    biasRollingMw: float | None = None
    origMaeMw: float | None = None
    expandingMaeMw: float | None = None
    rollingMaeMw: float | None = None
    matchedCount: int


class MonthSummaryModel(BaseModel):
    eligibleDaysBoth: int
    commonEvaluationObservations: int
    commonOriginalMetrics: MetricsModel
    commonExpandingMetrics: MetricsModel
    commonRollingMetrics: MetricsModel
    expandingDaysImproved: int
    expandingDaysWorsened: int
    rollingDaysImproved: int
    rollingDaysWorsened: int


class ProvenanceModel(BaseModel):
    windforDatasetUrl: str
    fuelhhDatasetUrl: str
    windforUrl: str
    fuelhhUrl: str
    windforParams: dict[str, str]
    fuelhhParams: dict[str, str]
    fuelTypeFilter: str
    contentHash: str
    snapshotId: str
    schemaVersion: int
    retrievedAt: str | None = None


class MissingDataModel(BaseModel):
    deliveryPeriods: int
    periodsWithForecast: int
    periodsWithActual: int
    periodsMissingForecast: int
    periodsMissingActual: int
    matchedPeriods: int


class ReproducibilityModel(BaseModel):
    algorithmVersion: str
    snapshotId: str
    parameters: dict[str, Any]
    sourceFieldDefinitions: dict[str, str]
    assumptions: list[str]
    hourlyAlignmentSensitivity: str


class AnalysisReportModel(BaseModel):
    reportType: str
    algorithmVersion: str
    selectedStrategy: str
    identity: IdentityModel
    provenance: ProvenanceModel
    scenarioDefinition: dict[str, Any]
    knownAtDecisionTime: dict[str, Any]
    learnedAfterDelivery: dict[str, Any]
    results: dict[str, Any]
    reproducibility: ReproducibilityModel
    limitations: list[str]
    markdown: str


class PopulationBlockModel(BaseModel):
    label: str
    note: str
    eligibleDays: int
    matchedObservations: int
    original: MetricsModel
    adjusted: MetricsModel | None = None
    daysImproved: int
    daysWorsened: int
    daysUnchanged: int


class ThreeWayPopulationModel(BaseModel):
    label: str
    note: str
    eligibleDays: int
    matchedObservations: int
    original: MetricsModel
    expanding: MetricsModel
    rolling: MetricsModel
    expandingDaysImproved: int
    expandingDaysWorsened: int
    expandingDaysUnchanged: int
    rollingDaysImproved: int
    rollingDaysWorsened: int
    rollingDaysUnchanged: int


class EvaluationPopulationsModel(BaseModel):
    expandingEligible: PopulationBlockModel
    rollingEligible: PopulationBlockModel
    commonThreeWay: ThreeWayPopulationModel


class ScenarioWorkbenchResponse(BaseModel):
    identity: IdentityModel
    exAnte: ExAnteModel
    periods: list[PeriodScenarioModel]
    retrospective: RetrospectiveModel
    dailyHistory: list[DailyHistoryItemModel]
    monthSummary: MonthSummaryModel
    evaluationPopulations: EvaluationPopulationsModel
    missingData: MissingDataModel
    provenance: ProvenanceModel
    reproducibility: ReproducibilityModel
    report: AnalysisReportModel
    limitations: list[str]
    requestedStrategy: str | None = None
