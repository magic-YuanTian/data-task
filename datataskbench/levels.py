"""The seven levels and 21 task types of DataTaskBench."""

LEVEL_NAMES = {"Q1": "Definition", "Q2": "Retrieve", "Q3": "Describe", "Q4": "Discover", "Q5": "Diagnose",
               "Q6": "Predict", "Q7": "Prescribe"}
TASK_TYPES = {
    "Q1.1": "Meaning", "Q1.2": "Document understanding",
    "Q2.1": "Filter", "Q2.2": "Aggregation & ranking", "Q2.3": "Exact value search", "Q2.4": "Document information retrieval",
    "Q3.1": "Trend", "Q3.2": "Cycles", "Q3.3": "Distribution", "Q3.4": "Relationship",
    "Q4.1": "Anomaly & spike detection", "Q4.2": "Distributional shift detection", "Q4.3": "Correlation detection",
    "Q4.open": "Level-wide open question",
    "Q5.1": "Find the reasons", "Q5.2": "Quantify contributions",
    "Q6.1": "Value forecasting", "Q6.2": "Predict the consequence", "Q6.3": "What-if simulation",
    "Q7.1": "Open-ended suggestions", "Q7.2": "Optimization under constraints",
}
