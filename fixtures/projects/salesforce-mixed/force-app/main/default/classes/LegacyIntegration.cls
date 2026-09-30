public class LegacyIntegration {

    // Positive (callout): plain-text endpoint and a credential built by hand.
    @future(callout=true)
    public static void pushOrders(String payload, String apiKey) {
        HttpRequest request = new HttpRequest();
        request.setEndpoint('http://erp.example.com/orders');
        request.setMethod('POST');
        request.setHeader('Authorization', 'Bearer ' + apiKey);
        request.setBody(payload);
        new Http().send(request);
    }

    // Positive (sharing): a query in a class without a sharing declaration.
    public static List<Opportunity> openOpportunities() {
        return [SELECT Id, Name FROM Opportunity WHERE IsClosed = false LIMIT 100];
    }
}
