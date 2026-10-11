//! Mathematical primitives and statistical distribution functions.

/// Evaluates the error function erf(x) using the Abramowitz & Stegun 7.1.26 approximation.
pub fn erf(x: f64) -> f64 {
    if x.is_nan() {
        return f64::NAN;
    }
    let sign = if x < 0.0 { -1.0 } else { 1.0 };
    let x_abs = x.abs();

    // Constants from A&S 7.1.26
    let a1 = 0.254829592;
    let a2 = -0.284496736;
    let a3 = 1.421413741;
    let a4 = -1.453152027;
    let a5 = 1.061405429;
    let p = 0.3275911;

    let t = 1.0 / (1.0 + p * x_abs);
    let poly = ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t;
    let y = 1.0 - poly * (-x_abs * x_abs).exp();

    sign * y
}

/// Standard normal cumulative distribution function $\Phi(z)$.
pub fn norm_cdf(z: f64) -> f64 {
    if z.is_nan() {
        return f64::NAN;
    }
    0.5 * (1.0 + erf(z / std::f64::consts::SQRT_2))
}

/// Inverse standard normal CDF $\Phi^{-1}(p)$ using Peter J. Acklam's rational approximation.
pub fn norm_inv(p: f64) -> f64 {
    if p <= 0.0 {
        return f64::NEG_INFINITY;
    }
    if p >= 1.0 {
        return f64::INFINITY;
    }
    if p.is_nan() {
        return f64::NAN;
    }

    // Coefficients in rational approximations
    const A: [f64; 6] = [
        -3.969683028665376e+01,
        2.209460984245205e+02,
        -2.759285104469687e+02,
        1.383_577_518_672_69e2,
        -3.066479806614716e+01,
        2.506628277459239e+00,
    ];
    const B: [f64; 5] = [
        -5.447609879822406e+01,
        1.615858368580409e+02,
        -1.556989798598866e+02,
        6.680131188771972e+01,
        -1.328068155288572e+01,
    ];
    const C: [f64; 6] = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e+00,
        -2.549732539343734e+00,
        4.374664141464968e+00,
        2.938163982698783e+00,
    ];
    const D: [f64; 4] = [
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e+00,
        3.754408661907416e+00,
    ];

    const P_LOW: f64 = 0.02425;
    const P_HIGH: f64 = 1.0 - P_LOW;

    if p < P_LOW {
        // Rational approximation for lower region
        let q = (-2.0 * p.ln()).sqrt();
        (((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5])
            / ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1.0)
    } else if p <= P_HIGH {
        // Rational approximation for central region
        let q = p - 0.5;
        let r = q * q;
        (((((A[0] * r + A[1]) * r + A[2]) * r + A[3]) * r + A[4]) * r + A[5]) * q
            / (((((B[0] * r + B[1]) * r + B[2]) * r + B[3]) * r + B[4]) * r + 1.0)
    } else {
        // Rational approximation for upper region
        let q = (-2.0 * (1.0 - p).ln()).sqrt();
        -(((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5])
            / ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1.0)
    }
}

/// Computes the sample mean of a slice.
pub fn mean(xs: &[f64]) -> f64 {
    if xs.is_empty() {
        return 0.0;
    }
    xs.iter().sum::<f64>() / xs.len() as f64
}

/// Computes the sample variance (N-1 denominator).
pub fn variance(xs: &[f64]) -> f64 {
    if xs.len() < 2 {
        return 0.0;
    }
    let m = mean(xs);
    let ss = xs.iter().map(|&x| (x - m).powi(2)).sum::<f64>();
    ss / (xs.len() - 1) as f64
}

/// Computes the sample skewness ($\gamma_3$).
pub fn skewness(xs: &[f64]) -> f64 {
    let n = xs.len() as f64;
    if n < 3.0 {
        return 0.0;
    }
    let m = mean(xs);
    let s2 = variance(xs);
    if s2 <= 1e-14 {
        return 0.0;
    }
    let s = s2.sqrt();
    let m3 = xs.iter().map(|&x| (x - m).powi(3)).sum::<f64>() / n;
    m3 / s.powi(3)
}

/// Computes the sample kurtosis ($\gamma_4$, unadjusted, normal = 3.0).
pub fn kurtosis(xs: &[f64]) -> f64 {
    let n = xs.len() as f64;
    if n < 4.0 {
        return 3.0;
    }
    let m = mean(xs);
    let s2 = variance(xs);
    if s2 <= 1e-14 {
        return 3.0;
    }
    let m4 = xs.iter().map(|&x| (x - m).powi(4)).sum::<f64>() / n;
    m4 / s2.powi(2)
}

/// Deterministic 64-bit SplitMix pseudo-random number generator.
#[derive(Clone, Debug)]
pub struct SplitMix64 {
    state: u64,
}

impl SplitMix64 {
    /// Creates a generator initialized with `seed`.
    pub fn new(seed: u64) -> Self {
        let state = if seed == 0 { 0xda942042e4dd58b5 } else { seed };
        Self { state }
    }

    /// Returns the next pseudorandom u64.
    pub fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(0x9e3779b97f4a7c15);
        let mut z = self.state;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d049bb133111eb);
        z ^ (z >> 31)
    }

    /// Returns a float in `[0.0, 1.0)`.
    pub fn next_f64(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 * (1.0 / (1u64 << 53) as f64)
    }

    /// Returns a uniformly distributed index in `0..upper`.
    pub fn next_usize(&mut self, upper: usize) -> usize {
        if upper <= 1 {
            0
        } else {
            (self.next_u64() % (upper as u64)) as usize
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_norm_cdf_and_inv() {
        assert!((norm_cdf(0.0) - 0.5).abs() < 1e-5);
        assert!((norm_cdf(1.96) - 0.975002).abs() < 1e-4);
        assert!((norm_cdf(-1.96) - 0.024998).abs() < 1e-4);

        assert!((norm_inv(0.5) - 0.0).abs() < 1e-5);
        assert!((norm_inv(0.975) - 1.95996).abs() < 1e-4);
        assert!((norm_inv(0.025) - (-1.95996)).abs() < 1e-4);
    }

    #[test]
    fn test_moments() {
        let xs = [1.0, 2.0, 3.0, 4.0, 5.0];
        assert_eq!(mean(&xs), 3.0);
        assert_eq!(variance(&xs), 2.5);
        assert_eq!(skewness(&xs), 0.0);
    }

    #[test]
    fn test_splitmix_determinism() {
        let mut rng1 = SplitMix64::new(42);
        let mut rng2 = SplitMix64::new(42);
        for _ in 0..100 {
            assert_eq!(rng1.next_u64(), rng2.next_u64());
        }
    }
}
