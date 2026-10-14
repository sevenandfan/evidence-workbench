"""B0 readable mathematical reference implementation; no B1 imports.

Author R interpreter/packages are not installed: this is an independent
implementation of the reviewed calculation path, not a native R rerun.
Distribution evaluation is shared SciPy; root finding and gamma math differ.
"""
import math
from scipy.stats import nct


def run(samples):
    output = []
    for row in samples:
        a, b = row["n_paid"], row["n_free"]
        nu = a + b - 2
        pooled_variance = ((a - 1) * row["sd_paid"]**2 + (b - 1) * row["sd_free"]**2) / nu
        raw = (row["mean_paid"] - row["mean_free"]) / math.sqrt(pooled_variance)
        factor = math.exp(math.lgamma(nu/2) - math.lgamma((nu-1)/2)) / math.sqrt(nu/2)
        estimate = factor * raw
        h = math.sqrt((a + b) / (a * b))
        alpha = .01 if a+b > 1000 else .05
        statistic = estimate / h

        def bisect(target):
            low, high = statistic - 128, statistic + 128
            assert nct.cdf(statistic, nu, low) > target > nct.cdf(statistic, nu, high)
            for _ in range(70):
                midpoint = (low + high) / 2
                if nct.cdf(statistic, nu, midpoint) > target:
                    low = midpoint
                else:
                    high = midpoint
            return h * (low + high) / 2

        output.append({"site": row["site"], "effect": estimate, "ci_low": bisect(1-alpha/2), "ci_high": bisect(alpha/2)})
    return output
