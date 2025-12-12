www.lafayetteinstrument.com . info@lafayetteinstrument.com
Model HSCK100A Operating Instructions
© 2016-2017 by Lafayette Instrument Company, Inc. All Rights Reserved. Rel. 3.21.17
Model HSCK100A
Scrambled Grid
Current Generator
Operating Instructions
3700 Sagamore Pkwy N 
Lafayette, IN 47904 USA
Tel: (765) 423-1505
Fax: (765) 423-4111
info@lafayetteinstrument.com 
www.lafayetteinstrument.com
MANHSCK100A
3700 Sagamore Pkwy N Lafayette, IN 47904 . Tel: (765) 423-1505
Lafayette Instrument Scrambled Grid Current Generator
Table of Contents
Operate in Local Mode 2
Operate in Remote Mode 2
Diagnostic Tests 3
Interface Details 4
Notes 7
To Operate in the LOCAL (Manual) Mode:
1. Connect a trigger signal source to the rear panel tip jacks. A simple hand switch, a relay, 
or an open collector driver is required. Be sure to observe the correct polarity if an active 
or open collector driver is used.
2. Connect the 8 pin shock output connector to the grid floor.
3. Turn the power on with the rear panel switch.
4. Set the REMOTE/LOCAL switch to LOCAL (down)
5. Push the SHOCK/SET switch to the SET position (down), and adjust the CURRENT SET 
knob until the meter shows the target grid shock current (in mA).
6. Release the SHOCK/SET switch. The unit is now ready to deliver grid shock current.
7. Shock current can now be triggered either with the SHOCK/SET switch on the front panel, 
or by applying a trigger signal to the rear panel pin jacks.
8. The front panel STATUS LED will glow green when the shock is on and flash red when 
shock is actually being delivered to a subject animal.
To Operate in the REMOTE Mode:
1. Connect a trigger signal source to the rear panel tip jacks, as above. This is optional since 
the shock current output can be controlled either from the tip jacks or through one of the 
DB25 remote port lines.
2. Connect the DB25 remote port to an external device capable of supplying current set 
point data. The remote port is compatible with most PC parallel ports, if appropriate PC 
software is provided.
3. Set the REMOTE/LOCAL switch to REMOTE.
4. The SGCG current setpoint can now be programmed from the remote data device, and the 
shock current can be triggered either from the remote device or through the rear panel pin 
jacks. The front panel controls are ignored, except the REMOTE/LOCAL switch.
The status LED shows green for shock-on and red for shock-delivered, as in LOCAL mode. 
In addition, the TEST_OUT line of the DB25 is asserted low during a shock-delivered event. It 
remains off (high) during shock-on.
2
www.lafayetteinstrument.com . info@lafayetteinstrument.com
Model HSCK100A Operating Instructions
Diagnostic Tests
The SGCG device can test the animal chamber grid for possible partial short circuits between 
the grid bars due to the buildup of animal debris during testing sessions. Such grid faults can 
lead to inconsistent and variable behavioral data.
In LOCAL mode, press and hold the TEST button for about a second. If the status LED stays 
RED, there is a grid fault problem. The grid should be inspected and cleaned. The grid is live 
during this test, so it should NOT be performed with an animal in the behavior chamber.
In REMOTE mode the grid fault TEST function is initiated by asserting the TEST line of the 
DB25 connector. The result is signaled by the front panel status LED (green= OK) and by the 
DB25 TEST_OUT line.
The status of the internal high voltage fuse can also be easily assessed.
In LOCAL mode, put the selector switch to SET and adjust the current to a non-zero value. The 
status LED will indicate red and the meter will indicate the current setpoint if the fuse is good.
In REMOTE mode, assert both the TEST_IN and TRIGGER DB25 lines. If the fuse is OK, the 
TEST_OUT line will be asserted low.
3
3700 Sagamore Pkwy N Lafayette, IN 47904 . Tel: (765) 423-1505
Lafayette Instrument Scrambled Grid Current Generator
Interface Details for Scrambled Grid Current Generator v3.10
10k
470
DB 25 Input Circuit
470
DB 25 Output Circuit
4
www.lafayetteinstrument.com . info@lafayetteinstrument.com
Model HSCK100A Operating Instructions
Trigger Input Circuit
(32V Max)
+ Input
- Input
JP 1
1
2
3
4
5
6
1500
Jumper Positions Trigger Function 
1-2, 3-4, 5-6* Contact closure or active pulldown 
2-3, 4-5 Active HI voltage (4.5V min) or current 
(2 mA min) drive
* These positions are the Default setting. The Default setting is 
required by ABET II for proper operation.
5
3700 Sagamore Pkwy N Lafayette, IN 47904 . Tel: (765) 423-1505
Lafayette Instrument Scrambled Grid Current Generator
DB25 Remote Connector Entry view
Pin # Function
1 TRIGGER
2 Data0 (0.02mA)
3 Data1 (0.04mA)
4 Data2 (0.08mA)
5 Data3 (0.16mA)
6 Data4 (0.32mA)
7 Data5 (0.64mA)
8 Data6 (1.28mA)
9 Data7 (2.56mA)
10 NC
11 TEST_OUT
12 NC
13 NC
Pin # Function
14 TEST_IN
15 NC
16 NC
17 NC
18 Ground
19 Ground
20 Ground
21 Ground
22 Ground
23 Ground
24 Ground
25 Ground
Notes: All signal lines, except TEST_OUT (pin 11), are active LOW. 
Signal levels are TTL compatible.
Using An HSCK100A For Two-Pole Applications
While the HSCK100A series instruments are designed for scrambled grid shock applications, 
they can also be used for two-pole applications, such as tail shock. The output waveform is 
symmetrical and biphasic. A simplified schematic for the HSCK100A when used in a two-pole 
application is shown in Figure A. The electrode load (E1 to E2) is driven by an H-bridge which 
makes electrode E1 positive during the A-drive phase and electrode E2 positive during the 
B-drive phase. The resulting output current waveform is shown in Figure B. 
To obtain the electrode load current waveform shown in Figure B, the two shock electrodes 
need to be connected as shown in Figure C. Figure C shows the Shock Out connector when 
looking at the rear panel of the HSCK100A.
6
www.lafayetteinstrument.com . info@lafayetteinstrument.com
Model HSCK100A Operating Instructions
7
Shock Count and Latency Data
In some applications (e.g. the defensive burying paradigm) it is useful to obtain a latency time 
between some event and shock delivery to the subject animal, and/or to count the number 
of shocks delivered. HSCK100A instruments facilitate these measurements by providing 
an output signal through the rear panel DB25 connector that is synchronized with shock 
delivery. The TEST_OUT signal from pin11 (shown on page 6) is TTL compatible. This signal 
is normally LOW, but transitions to HI when output shock current is actually delivered to a 
subject animal. The minimum duration of this signal is about 80 msec, and it continues for 
the full duration of shock delivery.
Figure A. Simplified output circuit for 
HSCK100 devices when used for two pole 
applications.
Drive A
Drive B
Drive C
Drive D
V+
E1 E2
Electrodes
Figure B. HSCK100 series current output
waveform when connected for two-pole
applications. Tp= 8.3 msec; Tr= 75.0 msec.
Tp
Tr
E2
E1
To Shock 
Electrodes
Figure C. The Shock Out connector pins
to use to obtain the two-pole current
waveforms shown in Figure B.
3700 Sagamore Pkwy N Lafayette, IN 47904 . Tel: (765) 423-1505
Terms and Conditions Lafayette Instrument Scrambled Grid Current Generator
LIC Worldwide Headquarters
Toll-Free: (800) 428-7545 (USA only)
Phone: (765) 423-1505
Fax: (765) 423-4111
sales@lafayetteinstrument.com
export@lafayetteinstrument.com (Outside the USA) 
Mailing Address: 
Lafayette Instrument Company 
PO Box 5729 
Lafayette, IN 47903, USA 
Lafayette Instrument Europe
Phone: +44 1509 817700
Fax: +44 1509 817701
Email: eusales@lafayetteinstrument.com
Phone, Fax, Email or Mail-in Orders
All orders need to be accompanied by a hard copy of your purchase order. All 
orders must include the following information:
• Quantity 
• Part Number 
• Description 
• Your purchase order number or method of pre-payment
• Your tax status (include tax-exempt numbers) 
• Shipping address for this order 
• Billing address for the invoice we’ll mail when this order is shipped 
• Signature and typed name of person authorized to order these products 
• Your telephone number 
• Your email address 
• Your FAX number 
Domestic Terms
There is a $50 minimum order. Open accounts can be extended to most 
recognized businesses. Net amount due 30 days from the date of shipment 
unless otherwise specified by us. Enclose payment with the order; charge with 
VISA, MasterCard, American Express, or pay COD. We must have a hard copy 
of your purchase order by mail, E-mail or fax. Students, individuals and private 
companies may call for a credit application.
International Payment Information
There is a $50 minimum order. Payment must be made in advance by: draft 
drawn on a major US bank; wire transfers to our account; charge with VISA, 
MasterCard, American Express, or confirmed irrevocable letter of credit. 
Proforma invoices will be provided upon request.
Exports
If ordering instrumentation for use outside the USA, please specify the country 
of ultimate destination, as well as the power requirements (110V/60Hz or 
220V/50Hz). Some model numbers for 220V/50Hz will have a “*C” suffix.
Quotations
Quotations are supplied upon request. Written quotations will include the price 
of goods, cost of shipping and handling, if requested, and estimated delivery 
time frame. Quotations are good for 30 days, unless otherwise noted. Following 
that time, prices are subject to change and will be re-quoted at your request.
Cancellations
Orders for custom products, custom assemblies or instruments built to 
customer specifications will be subject to a cancellation penalty of 100%. 
Payment for up to 100% of the invoice value of custom products may be required 
in advance. Cancellation for a standard Lafayette Instrument manufactured 
product once the product has been shipped will normally be assessed a charge 
of 25% of the invoice value, plus shipping charges. Resell items, like custom 
products, will be subject to a cancellation penalty of 100%.
Exchanges and Refunds
Please see the cancellation penalty as described above. No item may be returned 
without prior authorization of Lafayette Instrument Company and a Return 
Goods Authorization (RGA#) number which must be affixed to the shipping 
label of the returned goods. The merchandise should be packed well, insured 
for the full value and returned along with a cover letter explaining the reason for 
return. Unopened merchandise may be returned prepaid within thirty (30) days 
after receipt of the item and in the original shipping carton. Collect shipments 
will not be accepted. Product must be returned in saleable condition, and credit 
is subject to inspection of the merchandise. 
Repairs
Instrumentation may not be returned without first receiving a Return Goods 
Authorization Number (RGA). When returning instrumentation for service, please 
call Lafayette Instrument to receive a RGA number. Your RGA number will be 
good for 30 days. Address the shipment to:
Lafayette Instrument Company
3700 Sagamore Parkway North
Lafayette, IN 47904, USA.
Shipments cannot be received at the PO Box. The items should be packed 
well, insured for full value, and returned along with a cover letter explaining 
the malfunction. An estimate of repair will be given prior to completion ONLY 
if requested in your enclosed cover letter. We must have a hard copy of your 
purchase order by mail or fax, or repair work cannot commence for nonwarranty repairs.
Damaged Goods
Damaged instrumentation should not be returned to Lafayette Instrument prior to 
a thorough inspection. If a shipment arrives damaged, note damage on delivery 
bill and have the driver sign it to acknowledge the damage. Contact the delivery 
service, and they will file an insurance claim. If damage is not detected at the 
time of delivery, contact the carrier/shipper and request an inspection within 
10 days of the original delivery. Please call the Lafayette Instrument Customer 
Service Department for repair or replacement of the damaged merchandise.
Limited Warranty
Lafayette Instrument Company warrants equipment manufactured by the 
company to be free of defects in material and workmanship for a period of one 
year from the date of shipment, except as provided hereinafter. The original 
manufacturer’s warranty will be honored by Lafayette Instrument for items not 
manufactured by Lafayette Instrument Company, i.e. resell items. This assumes 
normal usage under commonly accepted operating parameters and excludes 
consumable products.
Warranty period for repairs or used instrumentation purchased from Lafayette 
Instrument is 90 days. Lafayette Instrument Company agrees either to 
repair or replace, at its sole option and free of part charges to the customer, 
instrumentation which, under proper and normal conditions of use, proves to 
be defective within the warranty period. Warranty for any parts of such repaired 
or replaced instrumentation shall be covered under the same limited warranty 
and shall have a warranty period of 90 days from the date of shipment or the 
remainder of the original warranty period whichever is greater. This warranty 
and remedy are given expressly and in lieu of all other warranties, expressed or 
implied, of merchantability or fitness for a particular purpose and constitutes 
the only warranty made by Lafayette Instrument Company. 
 
Lafayette Instrument Company neither assumes nor authorizes any person to 
assume for it any other liability in connection with the sale, installation, service or 
use of its instrumentation. Lafayette Instrument Company shall have no liability 
whatsoever for special, consequential, or punitive damages of any kind from any 
cause arising out of the sale, installation, service or use of its instrumentation. 
All products manufactured by Lafayette Instrument Company are tested and 
inspected prior to shipment. Upon prompt notification by the Customer, Lafayette 
Instrument Company will correct any defect in warranted equipment of its 
manufacture either, at its option, by return of the item to the factory, or shipment 
of a repaired or replacement part. Lafayette Instrument Company will not be 
obliged, however, to replace or repair any piece of equipment, which has been 
abused, improperly installed, altered, damaged, or repaired by others. Defects in 
equipment do not include decomposition, wear, or damage by chemical action 
or corrosion, or damage incurred during shipment.
Limited Obligations Covered by this Warranty
1. In the case of instruments not of Lafayette Instrument Company 
manufacture, the original manufacturer’s warranty applies.
2. Shipping charges under warranty are covered only in one direction. The 
customer is responsible for shipping charges to the factory if return of 
the part is required.
3. This warranty does not cover damage to components due to improper 
installation by the customer. 
4. Consumable and or expendable items, including but not limited to 
electrodes, lights, batteries, fuses, O-rings, gaskets, and tubing, are 
excluded from warranty.
5. Failure by the customer to perform normal and reasonable maintenance 
on instruments will void warranty claims.
6. If the original invoice for the instrument is issued to a company that 
is not the company of the end user, and not an authorized Lafayette 
Instrument Company distributor, then all requests for warranty must be 
processed through the company that sold the product to the end user, 
and not directly to Lafayette Instrument Company.
Export License
The U.S. Department of Commerce requires an export license for any polygraph 
system shipment with an ULTIMATE destination other than: Australia, Japan, 
New Zealand or any NATO Member Countries. It is against U.S. law to ship a 
Polygraph system to any other country without an export license. If the ultimate 
destination is not one of the above listed countries, contact us for the required 
license application forms.