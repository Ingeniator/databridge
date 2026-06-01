## Import flow

Pipeline order:
. Load source configuration
. Fetch candidate records
. Apply field filtering [Feature 003]
. Apply sampling [Feature 002]
. Apply masking [Feature 005]
. Extract asset links [Feature 004]
. Check asset availability [Feature 004]
. Persist result [Feature 001]
. Emit audit and metrics [Feature 001]
