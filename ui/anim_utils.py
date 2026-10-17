from PySide6.QtCore import QEasingCurve, QPointF


def fluid_easing() -> QEasingCurve:
    """PathInterpolator(0.2f, 0f, 0f, 1f) used across the Android app.

    Shared by the taskbar, filter bar and settings screen transitions.
    """
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.2, 0.0), QPointF(0.0, 1.0), QPointF(1.0, 1.0))
    return curve
