# Abandoned Cart Recovery Playbook

## When a cart counts as abandoned
A cart is abandoned when it has items, no completed payment, and no activity for at least 2 hours. Carts older than 14 days are considered cold and should not receive recovery messages; send them to the win-back flow instead.

## Recovery sequence
The first reminder goes out 2 to 6 hours after abandonment and contains no discount, only the cart contents and a one-tap payment link. Many shoppers were simply interrupted, so a plain reminder recovers them without giving away margin.
The second reminder goes out 24 to 48 hours after abandonment. It may include a small incentive (free shipping or 5 to 10 percent off) only if the cart value is above the store's average order value.
Never send more than two recovery messages for the same cart.

## Stage-specific handling
Carts abandoned at the checkout stage signal high intent. Prioritise these above carts abandoned at the product stage, and include a Razorpay payment link so the customer can complete the purchase in one step without logging in.
Carts abandoned at the payment stage because the payment failed must be handled by the Payment Failure Recovery playbook, not by a discount.

## Using payment links
Every recovery message should include a Razorpay Payment Link for the exact cart value, with the customer's name, email and phone prefilled and an expiry of 72 hours. Payment links remove friction because the customer does not need to rebuild the cart.

## High-value carts
Carts worth more than 3 times the average order value should be routed to WhatsApp or a personal email from the founder or support team rather than a generic template.
