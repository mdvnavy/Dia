import os
os.environ["TESTING"] = "true"
os.environ["GEMINI_API_KEY"] = ""
os.environ["JULES_API_KEY"] = ""
# Lockdown v2 gates every functional route behind Google sign-in. Legacy tests
# exercise those routes directly, so the suite runs with the local-dev escape
# hatch on (it is ignored on Cloud Run). Gate tests turn it off explicitly.
os.environ.setdefault("DIA_LOCAL_DEV_NO_SIGN_IN", "true")
